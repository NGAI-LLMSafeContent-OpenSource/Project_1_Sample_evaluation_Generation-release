"""evolution_panel.py - Prompt 演化可视化面板（Streamlit）。

展示跨 Round 的评分趋势、Issue 标签消退率、AI 改进方案时间线。
支持基于 AI 改进方案自动优化 Prompt 并保存新版本快照。
入口: render_evolution_page(qc_dir)
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st


# ============================================================
# LLM 基础设施（与 qc_panel 共享配置）
# ============================================================

_LLM_MODEL = "gpt-5.5"
_LLM_API_BASE = "https://right.codes/codex/v1"


def _get_api_key() -> str:
    """从项目根目录 .env 读取 LLM_API_KEY。"""
    env_path = Path(__file__).resolve().parents[2] / ".env"  # scripts/viewer -> scripts -> EvoBench
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("LLM_API_KEY="):
                return line.split("=", 1)[1].strip()
    return "EMPTY"


_OPTIMIZE_SYSTEM_PROMPT = """\
你是 prompt 工程专家，擅长根据人工评审反馈优化 LLM prompt。

输出格式要求：
请以 JSON 数组格式输出修改指令，每个元素包含:
- "find": 要替换的原文片段（精确匹配，包含足够上下文以确保唯一）
- "replace": 替换后的内容

示例:
```json
[
  {{"find": "要查找的原文片段", "replace": "替换后的内容"}},
  {{"find": "另一个要修改的片段", "replace": "修改后的内容"}}
]
```

规则:
1. 只输出 JSON 数组，不要输出任何解释
2. find 字段必须是当前代码中能唯一定位的片段
3. 只修改需要改进的部分，保留其他内容不变
4. 每个修改片段尽量精简，只包含必要的上下文
"""

_OPTIMIZE_USER_PROMPT = """\
请根据以下“AI 改进方案”，给出对当前 prompt 代码的具体修改。

## 当前 prompt 代码
{current_prompt_content}

## AI 改进方案
{ai_summary_content}
"""


def _apply_ai_improvement(current_prompt_path: Path, ai_summary_path: Path) -> str:
    """调用 LLM，根据 AI 改进方案生成优化后的 prompts.py 内容。"""
    import sys
    import concurrent.futures
    import traceback as _tb

    current_content = current_prompt_path.read_text(encoding="utf-8")
    ai_summary = ai_summary_path.read_text(encoding="utf-8")

    sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent))
    from benchmark.adapters.llm import build_llm_adapter

    adapter = build_llm_adapter(
        provider="openai_compatible",
        model=_LLM_MODEL,
        api_base=_LLM_API_BASE,
        api_key=_get_api_key(),
        timeout_seconds=600,
        request_retries=0,
    )

    user_prompt = _OPTIMIZE_USER_PROMPT.format(
        current_prompt_content=current_content,
        ai_summary_content=ai_summary,
    )

    # Streamlit 已有事件循环，用线程池规避 asyncio.run() 冲突
    def _run():
        return asyncio.run(adapter.complete(user_prompt, system=_OPTIMIZE_SYSTEM_PROMPT, temperature=0.2, max_tokens=4096))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run)
        result = future.result(timeout=600)

    if not result or not result.strip():
        raise RuntimeError("调用 LLM 未返回内容")

    # 解析 LLM 返回的 JSON 修改指令
    text = result.strip()
    # 清理可能的 markdown 代码块包裹
    if text.startswith("```json"):
        text = text[len("```json"):]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    import json as _json
    try:
        patches = _json.loads(text)
    except _json.JSONDecodeError as e:
        raise RuntimeError(f"LLM 返回的修改指令不是合法 JSON: {e}\n\n原始返回:\n{text[:500]}")

    if not isinstance(patches, list):
        raise RuntimeError(f"LLM 返回的不是 JSON 数组，而是: {type(patches).__name__}")

    # 逐个应用修改
    modified = current_content
    applied = 0
    failed: list[str] = []
    for i, patch in enumerate(patches):
        find_str = patch.get("find", "")
        replace_str = patch.get("replace", "")
        if not find_str:
            failed.append(f"patch[{i}]: find 字段为空")
            continue
        if find_str in modified:
            modified = modified.replace(find_str, replace_str, 1)
            applied += 1
        else:
            # 尝试忽略空白差异重试
            _find_normalized = " ".join(find_str.split())
            _content_normalized = " ".join(modified.split())
            if _find_normalized in _content_normalized:
                # 用行级匹配尝试定位
                lines = modified.splitlines(keepends=True)
                find_lines = find_str.splitlines()
                matched = False
                for li in range(len(lines) - len(find_lines) + 1):
                    chunk = "".join(lines[li:li + len(find_lines)])
                    if " ".join(chunk.split()) == _find_normalized:
                        modified = "".join(lines[:li]) + replace_str + "".join(lines[li + len(find_lines):])
                        applied += 1
                        matched = True
                        break
                if not matched:
                    failed.append(f"patch[{i}]: 找到近似匹配但无法精确定位")
            else:
                failed.append(f"patch[{i}]: 未在文件中找到匹配\n  find: {find_str[:80]}...")

    if applied == 0:
        raise RuntimeError(f"所有 {len(patches)} 个修改均未能应用:\n" + "\n".join(failed))

    return modified


def _save_new_snapshot(qc_dir: Path, new_prompt_content: str) -> Path:
    """保存优化后的 prompt 为新版本快照，同时更新当前运行版本。"""
    project_root = qc_dir.parent.parent  # data/qc -> data -> EvoBench
    snapshots_dir = project_root / ".snapshots"
    snapshots_dir.mkdir(exist_ok=True)

    # 确定新版本号
    max_ver = 0
    for d in snapshots_dir.iterdir():
        if d.is_dir():
            m = re.search(r"-v(\d+)-", d.name)
            if m:
                max_ver = max(max_ver, int(m.group(1)))
    new_ver = max_ver + 1
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    # 创建快照目录
    snapshot_dir = snapshots_dir / f"value_qa-v{new_ver}-{ts}"
    skill_dir = snapshot_dir / "skill"
    skill_dir.mkdir(parents=True)
    snapshot_prompt = skill_dir / "prompts.py"
    snapshot_prompt.write_text(new_prompt_content, encoding="utf-8")

    # 同时更新当前运行版本
    current_prompt = project_root / "benchmark" / "agents" / "skills" / "value_qa" / "prompts.py"
    if current_prompt.exists():
        current_prompt.write_text(new_prompt_content, encoding="utf-8")

    return snapshot_dir


# ============================================================
# 数据结构
# ============================================================

@dataclass
class RoundData:
    """单轮 QC 数据摘要。"""
    round_id: int
    tag: str
    label: str  # 展示用标签，如 "Round 2 (gpt55)"
    avg_scores: dict[str, float] = field(default_factory=dict)  # d1~d4
    issue_tag_freq: list[tuple[str, int]] = field(default_factory=list)
    issue_tag_freq_by_level: dict[str, list[tuple[str, int]]] = field(default_factory=lambda: {"high": [], "medium": [], "low": []})
    prompt_improvements: list[dict[str, Any]] = field(default_factory=list)
    ai_summary_path: Path | None = None


# ============================================================
# 数据加载
# ============================================================

def load_all_rounds(qc_dir: Path) -> list[RoundData]:
    """扫描 qc_dir 下所有 round 数据（基于 summary.json + scores.jsonl 双源发现）。"""
    results: list[RoundData] = []
    seen: set[str] = set()

    # 1) 从 summary.json 发现（含详细数据）
    for p in sorted(qc_dir.glob("round*_summary.json")):
        m = re.match(r"round(\d+)(?:_([^_]+))?_summary\.json$", p.name)
        if not m:
            continue
        round_id = int(m.group(1))
        tag = m.group(2) or ""
        key = f"{round_id}_{tag}"
        if key in seen:
            continue
        seen.add(key)

        with open(p) as fp:
            data = json.load(fp)

        overview = data.get("overview", {})
        label = f"Round {round_id}" + (f" ({tag})" if tag else "")

        rd = RoundData(
            round_id=round_id,
            tag=tag,
            label=label,
            avg_scores={
                "D1": overview.get("avg_d1", 0),
                "D2": overview.get("avg_d2", 0),
                "D3": overview.get("avg_d3", 0),
                "D4": overview.get("avg_d4", 0),
            },
            issue_tag_freq=[(t, c) for t, c in data.get("issue_tag_freq", [])],
            prompt_improvements=data.get("prompt_improvements", []),
        )

        # 查找对应的 ai_summary.md
        ai_md = qc_dir / f"round{round_id}{'_' + tag if tag else ''}_ai_summary.md"
        if ai_md.exists():
            rd.ai_summary_path = ai_md

        results.append(rd)

    # 2) 从 scores.jsonl 发现未被 summary.json 覆盖的 round
    for p in sorted(qc_dir.glob("round*_scores.jsonl")):
        m = re.match(r"round(\d+)(?:_([^_]+))?_scores\.jsonl$", p.name)
        if not m:
            continue
        round_id = int(m.group(1))
        tag = m.group(2) or ""
        key = f"{round_id}_{tag}"
        if key in seen:
            continue
        seen.add(key)

        label = f"Round {round_id}" + (f" ({tag})" if tag else "")

        # 从 scores 文件提取 issue 标签统计（按档位分组）
        _tag_cnt: dict[str, int] = {}
        _tag_cnt_by_lv: dict[str, dict[str, int]] = {"high": {}, "medium": {}, "low": {}}
        try:
            with open(p) as fp:
                for line in fp:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    by_lv = rec.get("issue_tags_by_level", {})
                    if by_lv:
                        for lv, lv_tags in by_lv.items():
                            if lv in _tag_cnt_by_lv:
                                for t in lv_tags:
                                    _tag_cnt[t] = _tag_cnt.get(t, 0) + 1
                                    _tag_cnt_by_lv[lv][t] = _tag_cnt_by_lv[lv].get(t, 0) + 1
                    else:
                        for t in rec.get("issue_tags", []):
                            _tag_cnt[t] = _tag_cnt.get(t, 0) + 1
        except Exception:
            pass

        _issue_freq = sorted(_tag_cnt.items(), key=lambda x: -x[1])
        _issue_freq_by_lv = {
            lv: sorted(cnts.items(), key=lambda x: -x[1])
            for lv, cnts in _tag_cnt_by_lv.items()
        }

        rd = RoundData(
            round_id=round_id,
            tag=tag,
            label=label,
            issue_tag_freq=[(t, c) for t, c in _issue_freq],
            issue_tag_freq_by_level=_issue_freq_by_lv,
        )

        # 查找对应的 ai_summary.md
        ai_md = qc_dir / f"round{round_id}{'_' + tag if tag else ''}_ai_summary.md"
        if ai_md.exists():
            rd.ai_summary_path = ai_md

        results.append(rd)

    # 按 round_id 排序
    results.sort(key=lambda r: (r.round_id, r.tag))
    return results


# ============================================================
# 渲染：各档可用率变化
# ============================================================

def _compute_usability_from_scores(qc_dir: Path) -> list[dict[str, Any]]:
    """从 scores.jsonl 文件计算各 Round 的 high/medium/low 可用率。

    usable 字段：0=不可用, 1=需修改, 2=可用
    可用率 = count(==2) / total
    """
    import re as _re
    results: list[dict[str, Any]] = []

    for p in sorted(qc_dir.glob("round*_scores.jsonl")):
        m = _re.match(r"round(\d+)(?:_([^_]+))?_scores\.jsonl$", p.name)
        if not m:
            continue
        round_id = int(m.group(1))
        tag = m.group(2) or ""
        label = f"Round {round_id}" + (f" ({tag})" if tag else "")

        with open(p) as fp:
            records = [json.loads(line) for line in fp if line.strip()]

        if not records:
            continue

        # 统计各档可用率
        counts: dict[str, dict[str, int]] = {
            "high": {"total": 0, "usable": 0, "fixable": 0},
            "medium": {"total": 0, "usable": 0, "fixable": 0},
            "low": {"total": 0, "usable": 0, "fixable": 0},
        }
        for rec in records:
            usable = rec.get("usable", {})
            if not isinstance(usable, dict):
                continue
            for level in ("high", "medium", "low"):
                val = usable.get(level)
                if val is not None:
                    counts[level]["total"] += 1
                    if val == 2:
                        counts[level]["usable"] += 1
                    elif val == 1:
                        counts[level]["fixable"] += 1

        row: dict[str, Any] = {"label": label, "round_id": round_id, "tag": tag}
        for level in ("high", "medium", "low"):
            total = counts[level]["total"]
            if total > 0:
                # 可用率 = (可用 + 勉强可用) / 总数，与 QC 评估报告口径一致
                row[f"{level}_可用率"] = (counts[level]["usable"] + counts[level]["fixable"]) / total
                row[f"{level}_需修改率"] = counts[level]["fixable"] / total
                row[f"{level}_不可用率"] = 1 - row[f"{level}_可用率"]
            else:
                row[f"{level}_可用率"] = None
                row[f"{level}_需修改率"] = None
                row[f"{level}_不可用率"] = None
        # 整组可用率（三档都 >=1 即整组可用）
        group_total = len([r for r in records if isinstance(r.get("usable"), dict)])
        group_pass = 0
        for rec in records:
            usable = rec.get("usable", {})
            if isinstance(usable, dict) and all(usable.get(l, 0) >= 1 for l in ("high", "medium", "low")):
                group_pass += 1
        row["整组可用率"] = group_pass / group_total if group_total > 0 else None
        results.append(row)

    return results


def render_usability_trend(qc_dir: Path):
    """展示各档可用率跨 Round 的变化。"""
    data = _compute_usability_from_scores(qc_dir)
    if not data:
        return  # 无可用率数据则不显示此板块

    # 检查是否有任何有效可用率数据
    rows = []
    for d in data:
        for level in ("high", "medium", "low"):
            rate = d.get(f"{level}_可用率")
            if rate is not None:
                rows.append({
                    "Round": d["label"],
                    "档位": level,
                    "可用率": rate,
                    "需修改率": d.get(f"{level}_需修改率", 0),
                })

    if not rows:
        return  # 无有效数据不显示

    st.subheader("各档可用率变化")
    df = pd.DataFrame(rows)

    # 使用 Plotly 绘制美观的折线图
    try:
        import plotly.graph_objects as go

        order = [d["label"] for d in data]
        _colors = {"high": "#4caf50", "medium": "#ff9800", "low": "#f44336"}
        _labels = {"high": "🟢 high", "medium": "🟡 medium", "low": "🔴 low"}

        fig = go.Figure()
        for level in ("high", "medium", "low"):
            lv_data = df[df["档位"] == level]
            # 确保按 round 顺序
            lv_data = lv_data.set_index("Round").reindex(order).reset_index()
            fig.add_trace(go.Scatter(
                x=lv_data["Round"],
                y=lv_data["可用率"],
                name=_labels[level],
                mode="lines+markers",
                line=dict(color=_colors[level], width=3),
                marker=dict(size=10, symbol="circle"),
                hovertemplate="%{x}<br>可用率: %{y:.0%}<extra></extra>",
            ))

        fig.update_layout(
            height=350,
            margin=dict(l=40, r=20, t=30, b=20),
            yaxis=dict(
                title="可用率",
                tickformat=".0%",
                range=[0, 1.05],
                gridcolor="#f0f0f0",
                showline=True,
                linecolor="#ddd",
            ),
            xaxis=dict(title="", gridcolor="#f0f0f0", showline=True, linecolor="#ddd"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
            plot_bgcolor="white",
            hovermode="x unified",
            # 图表边框
            shapes=[dict(
                type="rect", xref="paper", yref="paper",
                x0=0, y0=0, x1=1, y1=1,
                line=dict(color="#ddd", width=1),
            )],
        )
        st.plotly_chart(fig, use_container_width=True)
    except ImportError:
        # 回退到 Streamlit 原生折线图
        pivot = df.pivot(index="Round", columns="档位", values="可用率")
        order = [d["label"] for d in data]
        pivot = pivot.reindex(order)
        st.line_chart(pivot, height=300)

    st.caption("可用率 = (👍可用 + 🤏勉强可用) / 总数；1.0 = 全部可用或勉强可用")

    # 明细表格
    detail_rows = []
    for d in data:
        row = {"Round": d["label"]}
        for level in ("high", "medium", "low"):
            rate = d.get(f"{level}_可用率")
            row[f"{level} 可用"] = f"{rate:.0%}" if rate is not None else "-"
        row["整组可用"] = f"{d['整组可用率']:.0%}" if d.get("整组可用率") is not None else "-"
        detail_rows.append(row)

    st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True)


# ============================================================
# 渲染：Prompt 改进方案时间线
# ============================================================

def render_prompt_evolution(rounds: list[RoundData], qc_dir: Path):
    """按 Round 时间线展示改进方案。"""
    st.subheader("Prompt 改进方案时间线")

    if not rounds:
        st.info("暂无数据")
        return

    for rd in rounds:
        with st.expander(f"{rd.label}", expanded=False):
            # 1) Issue 标签与改进建议
            if rd.prompt_improvements:
                st.markdown("**基于 Issue 标签的改进建议：**")
                for item in rd.prompt_improvements:
                    tag = item.get("tag", "")
                    count = item.get("count", 0)
                    action = item.get("action", "")
                    st.markdown(f"- `{tag}` (x{count}) → {action}")
            elif rd.issue_tag_freq:
                # 没有 summary.json 但有从 scores 提取的 issue 标签
                st.markdown("**基于 Issue 标签的改进建议：**")
                _has_by_lv = any(rd.issue_tag_freq_by_level.get(lv) for lv in ("high", "medium", "low"))
                if _has_by_lv:
                    for lv, emoji in [("high", "🟢"), ("medium", "🟡"), ("low", "🔴")]:
                        lv_tags = rd.issue_tag_freq_by_level.get(lv, [])
                        if lv_tags:
                            items = " | ".join(f"`{t}` ×{c}" for t, c in lv_tags)
                            st.markdown(f"- {emoji} **{lv}**: {items}")
                else:
                    for tag, cnt in rd.issue_tag_freq:
                        st.markdown(f"- `{tag}` ×{cnt}")
                st.caption("ℹ️ 具体改进建议请见下方 AI 改进方案总结")
            else:
                st.caption("（该轮无 Issue 标签）")

            st.divider()

            # 2) AI 生成的改进方案（限高滚动，避免展开太长）
            if rd.ai_summary_path and rd.ai_summary_path.exists():
                st.markdown("**AI 改进方案总结：**")
                content = rd.ai_summary_path.read_text(encoding="utf-8")
                # 固定高度滚动容器，避免展开太长需要翻很远才能收起
                _escaped = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                st.markdown(f"""
                <div style="max-height:650px;overflow-y:auto;border:1px solid #e0e0e0;border-radius:6px;padding:12px 16px;background:#fafafa">
                <div style="font-size:14px;line-height:1.6">

{content}

                </div></div>
                """, unsafe_allow_html=True)
            else:
                st.caption("（该轮无 AI 改进方案）")


# ============================================================
# 渲染：Prompt 版本 Diff
# ============================================================

def _find_prompt_versions(qc_dir: Path) -> list[tuple[str, Path]]:
    """发现可用的 prompt 版本文件，按版本号排序。

    来源：
    1. .snapshots/ 目录下的 prompts.py 快照
    2. 当前 prompts.py（视为最新版本）
    """
    project_root = qc_dir.parent.parent  # data/qc -> data -> EvoBench root
    versions: list[tuple[str, Path]] = []

    # 1) 扫描 .snapshots/
    snapshots_dir = project_root / ".snapshots"
    if snapshots_dir.exists():
        for d in sorted(snapshots_dir.iterdir()):
            if not d.is_dir():
                continue
            prompt_file = d / "skill" / "prompts.py"
            if prompt_file.exists():
                # 从目录名提取版本标识，如 value_qa-v1-20260531_104153
                name_parts = d.name.split("-")
                version_label = name_parts[1] if len(name_parts) > 1 else d.name
                versions.append((version_label, prompt_file))

    # 2) 当前版本
    current = project_root / "benchmark" / "agents" / "skills" / "value_qa" / "prompts.py"
    if current.exists():
        # 动态确定当前版本号：快照最大版本号 + 1
        max_ver = 0
        for v_label, _ in versions:
            m = re.match(r"v(\d+)", v_label)
            if m:
                max_ver = max(max_ver, int(m.group(1)))

        # 如果当前文件与最新快照内容一致，不重复添加，只更新最新快照的标签
        _is_same_as_latest = False
        if versions:
            _latest_snap = versions[-1][1]
            if _latest_snap.exists():
                _cur_text = current.read_text(encoding="utf-8").strip()
                _snap_text = _latest_snap.read_text(encoding="utf-8").strip()
                if _cur_text == _snap_text:
                    _is_same_as_latest = True
                    # 给最新快照加上 current 标记
                    _old_label = versions[-1][0]
                    versions[-1] = (f"{_old_label} (current)", versions[-1][1])

        if not _is_same_as_latest:
            current_ver = max_ver + 1
            versions.append((f"v{current_ver} (current)", current))

    return versions


def _highlight_diff_html(left_lines: list[str], right_lines: list[str]) -> tuple[str, str]:
    """生成带差异高亮的 HTML 文本（左删除红底，右新增绿底）。"""
    import difflib

    sm = difflib.SequenceMatcher(None, left_lines, right_lines)
    left_html_parts: list[str] = []
    right_html_parts: list[str] = []

    def _esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for line in left_lines[i1:i2]:
                styled = f'<div style="padding:1px 4px;font-family:monospace;font-size:13px;white-space:pre-wrap">{_esc(line)}</div>'
                left_html_parts.append(styled)
                right_html_parts.append(styled)
        elif tag == "delete":
            for line in left_lines[i1:i2]:
                left_html_parts.append(
                    f'<div style="background:#fdd;padding:1px 4px;font-family:monospace;font-size:13px;white-space:pre-wrap">{_esc(line)}</div>'
                )
            # 右侧补空行保持对齐
            for _ in range(i2 - i1):
                right_html_parts.append(
                    '<div style="background:#f5f5f5;padding:1px 4px;font-family:monospace;font-size:13px">&nbsp;</div>'
                )
        elif tag == "insert":
            # 左侧补空行
            for _ in range(j2 - j1):
                left_html_parts.append(
                    '<div style="background:#f5f5f5;padding:1px 4px;font-family:monospace;font-size:13px">&nbsp;</div>'
                )
            for line in right_lines[j1:j2]:
                right_html_parts.append(
                    f'<div style="background:#dfd;padding:1px 4px;font-family:monospace;font-size:13px;white-space:pre-wrap">{_esc(line)}</div>'
                )
        elif tag == "replace":
            for line in left_lines[i1:i2]:
                left_html_parts.append(
                    f'<div style="background:#fdd;padding:1px 4px;font-family:monospace;font-size:13px;white-space:pre-wrap">{_esc(line)}</div>'
                )
            for line in right_lines[j1:j2]:
                right_html_parts.append(
                    f'<div style="background:#dfd;padding:1px 4px;font-family:monospace;font-size:13px;white-space:pre-wrap">{_esc(line)}</div>'
                )
            # 补齐较短一侧
            diff_count = (i2 - i1) - (j2 - j1)
            if diff_count > 0:
                for _ in range(diff_count):
                    right_html_parts.append(
                        '<div style="background:#f5f5f5;padding:1px 4px;font-family:monospace;font-size:13px">&nbsp;</div>'
                    )
            elif diff_count < 0:
                for _ in range(-diff_count):
                    left_html_parts.append(
                        '<div style="background:#f5f5f5;padding:1px 4px;font-family:monospace;font-size:13px">&nbsp;</div>'
                    )

    left_html = "\n".join(left_html_parts)
    right_html = "\n".join(right_html_parts)
    return left_html, right_html


def render_prompt_diff(qc_dir: Path):
    """左右并排展示两个 Prompt 版本，差异行高亮标注。"""
    # 锚点标记，用于按钮跳转
    st.markdown('<div id="prompt-diff-anchor"></div>', unsafe_allow_html=True)
    st.subheader("Prompt 版本对比")

    # 如果是从按钮跳转过来的，注入 JS 滚动到此处
    if st.session_state.pop("_evo_scroll_to_diff", False):
        st.markdown(
            '<script>document.getElementById("prompt-diff-anchor").scrollIntoView({behavior:"smooth"});</script>',
            unsafe_allow_html=True,
        )

    versions = _find_prompt_versions(qc_dir)
    if len(versions) < 2:
        st.info("需要至少 2 个版本才能对比")
        return

    labels = [v[0] for v in versions]
    # 默认选中最新两个版本对比（倒数第二 vs 倒数第一）
    default_left = max(0, len(labels) - 2)
    default_right = len(labels) - 1
    col_sel1, col_sel2 = st.columns(2)
    with col_sel1:
        left_idx = st.selectbox("左侧版本", range(len(labels)),
                                format_func=lambda i: labels[i], index=default_left,
                                key="diff_left")
    with col_sel2:
        right_idx = st.selectbox("右侧版本", range(len(labels)),
                                 format_func=lambda i: labels[i],
                                 index=default_right,
                                 key="diff_right")

    if left_idx == right_idx:
        st.warning("请选择两个不同的版本")
        return

    left_text = versions[left_idx][1].read_text(encoding="utf-8")
    right_text = versions[right_idx][1].read_text(encoding="utf-8")

    # 统计差异
    import difflib
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    diff = list(difflib.unified_diff(left_lines, right_lines, lineterm=""))
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    st.caption(
        f"{labels[left_idx]}: {len(left_lines)} 行 | "
        f"{labels[right_idx]}: {len(right_lines)} 行 | "
        f"差异: +{added} / -{removed}  "
        f"(🟥 红底=删除, 🟩 绿底=新增)"
    )

    # 生成带高亮的 HTML
    left_html, right_html = _highlight_diff_html(left_lines, right_lines)

    # 左右并排 + 联动滚动：放在同一个滚动容器中，用 CSS grid 两列布局
    combined_html = f"""
    <div style="max-height:650px;overflow-y:auto;border:1px solid #ddd;border-radius:6px;padding:0">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:0">
        <div style="border-right:2px solid #ccc">
          <div style="position:sticky;top:0;background:#f8f9fa;padding:6px 8px;border-bottom:1px solid #ddd;font-weight:bold;font-size:14px;z-index:1">{labels[left_idx]}</div>
          <div style="padding:4px">{left_html}</div>
        </div>
        <div>
          <div style="position:sticky;top:0;background:#f8f9fa;padding:6px 8px;border-bottom:1px solid #ddd;font-weight:bold;font-size:14px;z-index:1">{labels[right_idx]}</div>
          <div style="padding:4px">{right_html}</div>
        </div>
      </div>
    </div>
    """
    st.markdown(combined_html, unsafe_allow_html=True)


# ============================================================
# 主入口
# ============================================================

def render_evolution_page(qc_dir: Path):
    """Prompt 演化面板主入口。"""
    st.header("Prompt 演化追踪")

    rounds = load_all_rounds(qc_dir)
    project_root = qc_dir.parent.parent
    current_prompt_path = project_root / "benchmark" / "agents" / "skills" / "value_qa" / "prompts.py"

    # ======== 操作按钮：应用 AI 改进方案 ========
    # 找到最新的 ai_summary.md
    _latest_summary: Path | None = None
    for rd in reversed(rounds):
        if rd.ai_summary_path and rd.ai_summary_path.exists():
            _latest_summary = rd.ai_summary_path
            break

    if _latest_summary:
        # 检查是否已基于最新 round 的改进方案生成了新 prompt 版本
        # 逻辑：round N 的 AI 改进方案 → 应产出 prompt v(N+1)
        # 如果最新快照版本号 > 最新有 AI 改进方案的 round 编号，说明已完成
        _already_optimized = False
        _snapshots_dir = project_root / ".snapshots"
        _max_snap_ver = 0
        if _snapshots_dir.exists():
            for d in _snapshots_dir.iterdir():
                if d.is_dir():
                    m = re.search(r"-v(\d+)-", d.name)
                    if m:
                        _max_snap_ver = max(_max_snap_ver, int(m.group(1)))
        # 找到最新有 AI 改进方案的 round 编号
        _latest_ai_round = 0
        for rd in rounds:
            if rd.ai_summary_path and rd.ai_summary_path.exists():
                _latest_ai_round = max(_latest_ai_round, rd.round_id)
        # prompt 版本应该比最新 AI round 多 1（round N → v(N+1))
        if _max_snap_ver > _latest_ai_round:
            _already_optimized = True

        # 浅蓝色按钮样式
        st.markdown("""
        <style>
        .evo-apply-btn-area + div [data-testid="stButton"] button {
            background-color: #e3f2fd !important;
            color: #1565c0 !important;
            border: 1px solid #90caf9 !important;
        }
        .evo-apply-btn-area + div [data-testid="stButton"] button:hover {
            background-color: #bbdefb !important;
        }
        </style>
        <div class="evo-apply-btn-area"></div>
        """, unsafe_allow_html=True)
        if st.button("🚀 应用 AI 改进方案优化 Prompt", key="evo_apply_latest", use_container_width=True):
            if _already_optimized:
                st.info("ℹ️ 当前 Prompt 已是最新优化版本（与最新快照一致），无需重复执行。请查看下方 Prompt 版本对比。")
            elif st.session_state.get("_evo_last_applied") == str(_latest_summary):
                st.info("ℹ️ 已基于该方案优化过 Prompt，无需重复执行。请查看下方 Prompt 版本对比。")
            else:
                st.session_state["_evo_apply_summary"] = str(_latest_summary)
    else:
        st.button("🚀 应用 AI 改进方案优化 Prompt", key="evo_apply_latest_disabled",
                  use_container_width=True, disabled=True)
        st.warning("⚠️ 请先在 QC 评估界面完成评估并生成整体评估报告（AI 改进方案）。")

    # ======== LLM 优化流程 ========
    _apply_path = st.session_state.get("_evo_apply_summary")
    if _apply_path and current_prompt_path.exists():
        _summary_p = Path(_apply_path)
        # 如果还没有生成结果，调用 LLM
        if "_evo_new_prompt" not in st.session_state:
            try:
                with st.spinner("正在调用 LLM 生成优化后的 Prompt（可能需要 1-3 分钟）..."):
                    new_content = _apply_ai_improvement(current_prompt_path, _summary_p)
                st.session_state["_evo_new_prompt"] = new_content
            except Exception as e:
                import traceback
                tb_str = traceback.format_exc()
                st.error(f"❌ LLM 调用失败: {e}")
                st.code(tb_str, language="text")
                st.session_state.pop("_evo_apply_summary", None)

        # 展示 diff 预览 + 确认/放弃按钮
        if "_evo_new_prompt" in st.session_state:
            st.success("✅ LLM 已生成优化结果，请查看下方 diff 并确认。")
            _old_lines = current_prompt_path.read_text(encoding="utf-8").splitlines()
            _new_lines = st.session_state["_evo_new_prompt"].splitlines()
            left_html, right_html = _highlight_diff_html(_old_lines, _new_lines)

            import difflib
            _diff = list(difflib.unified_diff(_old_lines, _new_lines, lineterm=""))
            _added = sum(1 for l in _diff if l.startswith("+") and not l.startswith("+++"))
            _removed = sum(1 for l in _diff if l.startswith("-") and not l.startswith("---"))
            st.caption(f"当前: {len(_old_lines)} 行 | 优化后: {len(_new_lines)} 行 | 差异: +{_added} / -{_removed}")

            combined_html = f"""
            <div style="max-height:500px;overflow-y:auto;border:1px solid #ddd;border-radius:6px;padding:0">
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:0">
                <div style="border-right:2px solid #ccc">
                  <div style="position:sticky;top:0;background:#f8f9fa;padding:6px 8px;border-bottom:1px solid #ddd;font-weight:bold;font-size:14px;z-index:1">当前版本</div>
                  <div style="padding:4px">{left_html}</div>
                </div>
                <div>
                  <div style="position:sticky;top:0;background:#f8f9fa;padding:6px 8px;border-bottom:1px solid #ddd;font-weight:bold;font-size:14px;z-index:1">优化后</div>
                  <div style="padding:4px">{right_html}</div>
                </div>
              </div>
            </div>
            """
            st.markdown(combined_html, unsafe_allow_html=True)

            col_confirm, col_discard = st.columns(2)
            with col_confirm:
                if st.button("✅ 确认并保存为新版本", key="evo_confirm_save", type="primary", use_container_width=True):
                    snapshot_dir = _save_new_snapshot(qc_dir, st.session_state["_evo_new_prompt"])
                    # 记录已应用的方案，避免重复执行
                    _applied_summary = st.session_state.get("_evo_apply_summary", "")
                    st.session_state["_evo_last_applied"] = _applied_summary
                    st.session_state.pop("_evo_new_prompt", None)
                    st.session_state.pop("_evo_apply_summary", None)
                    st.success(f"✅ 已保存新版本快照: `{snapshot_dir.name}`")
                    st.balloons()
                    st.rerun()
            with col_discard:
                if st.button("❌ 放弃", key="evo_discard", use_container_width=True):
                    st.session_state.pop("_evo_new_prompt", None)
                    st.session_state.pop("_evo_apply_summary", None)
                    st.rerun()

    st.divider()

    # 概要卡片
    # QC 轮次基于 scores 文件统计（按 round_id 去重，同一轮不同 tag 不重复计数）
    _round_ids: set[int] = set()
    for p in qc_dir.glob("round*_scores.jsonl"):
        m = re.match(r"round(\d+)", p.name)
        if m:
            _round_ids.add(int(m.group(1)))
    _qc_round_count = len(_round_ids)
    col1, col2, col3 = st.columns(3)
    col1.metric("QC 轮次", f"{_qc_round_count} 轮")
    _versions = _find_prompt_versions(qc_dir)
    col2.metric("Prompt 版本数", f"{len(_versions)} 个")
    if _versions:
        col3.metric("最新版本", _versions[-1][0])

    st.divider()

    # 三大板块
    render_usability_trend(qc_dir)
    st.divider()
    render_prompt_evolution(rounds, qc_dir)
    st.divider()
    render_prompt_diff(qc_dir)
