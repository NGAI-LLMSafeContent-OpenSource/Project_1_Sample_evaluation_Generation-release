"""qc_panel.py - QC 评估页面渲染（Streamlit）。

入口: render_qc_page(samples_path, index_path, scores_path)
"""
from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import streamlit as st

from viewer.qc_data import (
    ISSUE_TAGS,
    ISSUE_TAGS_HIGH,
    ISSUE_TAGS_LOW,
    ISSUE_TAGS_MEDIUM,
    TripletScore,
    compute_progress,
    load_index,
    load_samples,
    load_scores,
    samples_by_triplet,
    save_scores,
    upsert_score,
)

# ---------- 工具 ----------


def _level_emoji(lv: str) -> str:
    return {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(lv, "⚪")


def _get_qa(sample: dict[str, Any]) -> tuple[str, str]:
    out = sample.get("output") or {}
    q, a = "", ""
    for art in out.get("artifacts") or []:
        if art.get("key") == "x" or art.get("role") == "question":
            q = art.get("value", "")
        elif art.get("key") == "y" or art.get("role") == "answer":
            a = art.get("value", "")
    return q, a


def _get_source_doc(sample: dict[str, Any]) -> tuple[str, str, str]:
    """返回 (title, url, content) 从 input.documents[0]"""
    docs = (sample.get("input") or {}).get("documents") or []
    if not docs:
        return "", "", ""
    d = docs[0]
    return d.get("title", ""), d.get("url", ""), d.get("content", "")


def _highlight_annotations(answer: str, annotations: list[dict]) -> str:
    """在答案中用 HTML <mark> 高亮 value_annotations 的 text 片段。"""
    if not annotations or not answer:
        return answer
    # 按 start 降序排列，从后往前插入 mark 标签（避免偏移）
    spans = []
    for a in annotations:
        if a.get("field") != "answer":
            continue
        text = a.get("text", "")
        start = a.get("start")
        end = a.get("end")
        polarity = a.get("polarity", "neutral")
        if start is not None and end is not None and 0 <= start < end <= len(answer):
            spans.append((start, end, polarity))
        elif text and text in answer:
            # fallback: 用子串匹配
            idx = answer.find(text)
            if idx >= 0:
                spans.append((idx, idx + len(text), polarity))
    # 去重叠、按 start 降序
    spans.sort(key=lambda x: x[0], reverse=True)
    colors = {"positive": "#c8f7c5", "negative": "#f7c5c5", "neutral": "#f7f0c5"}
    result = answer
    for s, e, pol in spans:
        color = colors.get(pol, "#e0e0e0")
        result = result[:s] + f'<mark style="background:{color};padding:1px 2px;border-radius:3px">' + result[s:e] + '</mark>' + result[e:]
    return result


# ---------- 单档卡片 ----------


def _render_level_card(level: str, sample: dict[str, Any]) -> None:
    emoji = _level_emoji(level)
    q, a = _get_qa(sample)
    meta = sample.get("metadata") or {}
    annotations = sample.get("value_annotations") or []
    medium_pattern = meta.get("medium_pattern", "") if level == "medium" else ""

    title = f"{emoji} **{level.upper()}**"
    if medium_pattern:
        title += f" · `pattern={medium_pattern}`"
    st.markdown(title)
    st.markdown(f"**Q**: {q}")
    # 答案中高亮 value_annotations
    highlighted_a = _highlight_annotations(a, annotations)
    st.markdown(f"**A**: {highlighted_a}", unsafe_allow_html=True)


# ---------- 评估控件 ----------

_D2_LABELS = {0: "未评", 1: "三档雷同", 2: "区分但不强", 3: "三档语义清晰对立"}
_D3_LABELS = {0: "未评", 1: "套话/空泛", 2: "立场对但偏抽象", 3: "具体引用 + 立场鲜明"}
_D4_LABELS = {0: "未评 / 无 annotation", 1: "严重错误", 2: "部分偏移/标注不足", 3: "全部正确"}


def _radio_score(label: str, key: str, current: int, opts: dict[int, str]) -> int:
    options = list(opts.keys())
    idx = options.index(current) if current in options else 0
    chosen = st.radio(label, options, index=idx, format_func=lambda v: opts[v], key=key, horizontal=True)
    return int(chosen)


def _render_score_form(score: TripletScore, triplet_idx: int, round_prefix: str = "") -> TripletScore:
    """渲染评估控件，返回更新后的 TripletScore。

    round_prefix: 用于隔离不同 Round 的 widget key，避免 Streamlit session_state 缓存串扰。
    """
    _kp = f"{round_prefix}_" if round_prefix else ""
    # 完整度提示
    missing: list[str] = []
    for lv in ("high", "medium", "low"):
        if score.usable.get(lv) is None:
            missing.append(f"可用性·{lv}")
    if missing:
        st.warning(f"⚠️ 本组还缺：{', '.join(missing)}")
    else:
        st.success("✅ 本组必填项已就绪")

    st.markdown("#### 📝 评估")

    # 1. 样本可用性
    _usable_options = ["⭕ 未标", "👍 可用", "🤏 勉强可用", "👎 不可用"]
    st.markdown("**1. 样本可用性（每档独立）**")
    _usable_vals: dict[str, int | None] = {}
    uc_h, uc_m, uc_l = st.columns(3)
    for _col, _lv, _emoji in [(uc_h, "high", "🟢"), (uc_m, "medium", "🟡"), (uc_l, "low", "🔴")]:
        with _col:
            _cur_val = score.usable.get(_lv)
            if _cur_val == 2:
                _cur = 1
            elif _cur_val == 1:
                _cur = 2
            elif _cur_val == 0:
                _cur = 3
            else:
                _cur = 0
            _choice = st.radio(
                f"{_emoji} {_lv}", _usable_options, index=_cur,
                key=f"{_kp}usable_{_lv}_{triplet_idx}", horizontal=True
            )
            if _choice == _usable_options[1]:
                _usable_vals[_lv] = 2
            elif _choice == _usable_options[2]:
                _usable_vals[_lv] = 1
            elif _choice == _usable_options[3]:
                _usable_vals[_lv] = 0
            else:
                _usable_vals[_lv] = None

    # Issue 标签（按档位分别选择，各档有专属标签）
    st.markdown("**2. Issue 标签（按档位，可多选）**")
    _it_h, _it_m, _it_l = st.columns(3)
    _existing_by_lv = score.issue_tags_by_level or {"high": [], "medium": [], "low": []}
    with _it_h:
        tags_high = st.multiselect("🟢 high", ISSUE_TAGS_HIGH,
                                   default=[t for t in _existing_by_lv.get("high", []) if t in ISSUE_TAGS_HIGH],
                                   key=f"{_kp}tags_high_{triplet_idx}")
    with _it_m:
        tags_medium = st.multiselect("🟡 medium", ISSUE_TAGS_MEDIUM,
                                     default=[t for t in _existing_by_lv.get("medium", []) if t in ISSUE_TAGS_MEDIUM],
                                     key=f"{_kp}tags_med_{triplet_idx}")
    with _it_l:
        tags_low = st.multiselect("🔴 low", ISSUE_TAGS_LOW,
                                  default=[t for t in _existing_by_lv.get("low", []) if t in ISSUE_TAGS_LOW],
                                  key=f"{_kp}tags_low_{triplet_idx}")
    # 合并去重
    issue_tags = list(dict.fromkeys(tags_high + tags_medium + tags_low))
    issue_tags_by_level = {"high": tags_high, "medium": tags_medium, "low": tags_low}

    # 评审意见（按档位）
    st.markdown("**✍️ 评审意见（按档位）**")
    _notes = score.notes or {"general": score.note, "high": "", "medium": "", "low": ""}
    nc_gen, nc_h, nc_m, nc_l = st.columns(4)
    with nc_gen:
        note_general = st.text_area("✍️ 整组", value=_notes.get("general", ""), key=f"{_kp}note_gen_{triplet_idx}", height=80)
    with nc_h:
        note_high = st.text_area("🟢 high", value=_notes.get("high", ""), key=f"{_kp}note_high_{triplet_idx}", height=80)
    with nc_m:
        note_medium = st.text_area("🟡 medium", value=_notes.get("medium", ""), key=f"{_kp}note_med_{triplet_idx}", height=80)
    with nc_l:
        note_low = st.text_area("🔴 low", value=_notes.get("low", ""), key=f"{_kp}note_low_{triplet_idx}", height=80)

    _new_notes = {"general": note_general, "high": note_high, "medium": note_medium, "low": note_low}
    # note 字段兼容：拼接所有非空评审意见
    _note_combined = "; ".join(f"[{k}]{v}" for k, v in _new_notes.items() if v.strip())

    return TripletScore(
        triplet_id=score.triplet_id,
        d1_fact_judgable=score.d1_fact_judgable,
        d2_triplet_discrim=score.d2_triplet_discrim,
        d3_answer_fullness=score.d3_answer_fullness,
        d4_annotation_correct=score.d4_annotation_correct,
        usable=_usable_vals,
        issue_tags=issue_tags,
        issue_tags_by_level=issue_tags_by_level,
        note=_note_combined,
        notes=_new_notes,
        reviewer=score.reviewer,
        ts=score.ts,
    )


# ---------- LLM 总结 ----------

_REVIEW_SUMMARY_PROMPT = """# 你的角色
你是一个数据质量专家，负责根据人工评审意见总结出 prompt 改进方案。

# 背景
我们有一个“价值观问答三联组”生成任务：给定一篇源文档和一个 fact，生成 high/medium/low 三档答案，并在每档答案中标注 value_annotations。
人工评审了一批生成结果，给出了以下评审意见（按档位分组）和 Issue 标签频次。

# 评审意见
{notes}

# Issue 标签频次
{issue_tags}

# 输出要求
请总结出 **可执行的 prompt 改进方案**，包括：
1. 问题归类（哪些问题最突出）
2. 每个问题对应的具体改 prompt 建议（修改哪部分、如何改）
3. 是否需要加新的约束或示例

用中文 Markdown 格式输出，简洁实用，直接给出建议，不要废话。
"""


# 评审意见汇总使用的模型
_REVIEW_LLM_MODEL = "gpt-5.5"
_REVIEW_LLM_API_BASE = "https://right.codes/codex/v1"


def _get_api_key() -> str:
    """从项目根目录 .env 读取 LLM_API_KEY。"""
    env_path = Path(__file__).resolve().parents[2] / ".env"  # scripts/viewer -> scripts -> EvoBench
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("LLM_API_KEY="):
                return line.split("=", 1)[1].strip()
    return "EMPTY"


def _llm_summarize_notes(notes_text: str, tag_cnt_by_level: dict[str, Counter]) -> str:
    """调用 LLM 总结评审意见，生成改进方案。"""
    _has_tags = any(c for c in tag_cnt_by_level.values())
    if not notes_text.strip() and not _has_tags:
        return "（无评审意见且无 Issue 标签，无法生成改进方案）"
    try:
        import sys
        import concurrent.futures
        import traceback as _tb
        sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent))
        from benchmark.adapters.llm import build_llm_adapter
        adapter = build_llm_adapter(
            provider="openai_compatible",
            model=_REVIEW_LLM_MODEL,
            api_base=_REVIEW_LLM_API_BASE,
            api_key=_get_api_key(),
            timeout_seconds=180,
        )
        # 按档位格式化 issue 标签
        _tag_lines: list[str] = []
        for lv, emoji in [("high", "🟢"), ("medium", "🟡"), ("low", "🔴")]:
            lv_cnt = tag_cnt_by_level.get(lv)
            if lv_cnt:
                _tag_lines.append(f"[{emoji} {lv}档]")
                _tag_lines.extend(f"- {tag} \u00d7{cnt}" for tag, cnt in lv_cnt.most_common())
        issue_tags_text = "\n".join(_tag_lines) if _tag_lines else "(无)"
        # 如果没有文字评审意见但有 issue 标签，用标签作为主要输入
        if not notes_text.strip():
            notes_text = "(未填写文字评审意见，请基于 Issue 标签生成改进方案)"
        prompt = _REVIEW_SUMMARY_PROMPT.format(notes=notes_text, issue_tags=issue_tags_text)

        # Streamlit 已有事件循环，用线程池规避 asyncio.run() 冲突
        def _run():
            return asyncio.run(adapter.complete(prompt, temperature=0.3, max_tokens=1500))

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run)
            result = future.result(timeout=180)
        return result.strip() if result else "(调用失败，未返回内容)"
    except Exception as e:
        tb_str = _tb.format_exc()
        return f"⚠️ LLM 调用失败: {e}\n\n<details><summary>详细错误</summary>\n\n```\n{tb_str}\n```\n</details>"


# ---------- 内联报告 ----------


def _generate_inline_report(index: list[dict[str, Any]], scores: dict[str, TripletScore]) -> None:
    """在 viewer 界面内展示整体评估报告。"""
    completed = [sc for sc in scores.values() if sc.is_complete]
    nc = len(completed)
    if nc == 0:
        st.warning("还没有完成任何评估，无法生成报告。")
        return

    # 可用率（按档位）
    _usable_lines = []
    for lv, emoji in [("high", "🟢"), ("medium", "🟡"), ("low", "🔴")]:
        lv_ok = sum(1 for sc in completed if sc.usable.get(lv) == 2)
        lv_mid = sum(1 for sc in completed if sc.usable.get(lv) == 1)
        lv_bad = sum(1 for sc in completed if sc.usable.get(lv) == 0)
        pct = f"{(lv_ok+lv_mid)/nc*100:.0f}%" if nc else "0%"
        _usable_lines.append(f"{emoji} {lv}: 👍{lv_ok} 🤏{lv_mid} 👎{lv_bad} ({pct})")

    # Issue 标签（按档位分组统计）
    tag_cnt: Counter = Counter()  # 总计（用于 LLM prompt）
    tag_cnt_by_level: dict[str, Counter] = {"high": Counter(), "medium": Counter(), "low": Counter()}
    for sc in completed:
        tag_cnt.update(sc.issue_tags or [])
        for lv in ("high", "medium", "low"):
            tag_cnt_by_level[lv].update(sc.issue_tags_by_level.get(lv, []))

    # 按维度拆解
    # 建 tid -> entry 映射
    _tid_entry = {e["triplet_id"]: e for e in index}
    _by_dim: dict[str, list[TripletScore]] = {}
    for sc in completed:
        entry = _tid_entry.get(sc.triplet_id)
        if entry:
            dim = entry.get("evaluation_dimension", "?")
            _by_dim.setdefault(dim, []).append(sc)

    # 渲染
    st.markdown("---")
    st.markdown("## 📊 整体评估报告")
    st.markdown(f"**已评组数**: {nc} / {len(index)}")
    st.markdown(f"**可用率**: {' &nbsp;|&nbsp; '.join(_usable_lines)}")

    if tag_cnt:
        st.markdown("**Issue 分布（按档位）**:")
        for lv, emoji in [("high", "🟢"), ("medium", "🟡"), ("low", "🔴")]:
            lv_tags = tag_cnt_by_level[lv]
            if lv_tags:
                items = " &nbsp;|".join(f"`{t}` ×{c}" for t, c in lv_tags.most_common())
                st.markdown(f"- {emoji} **{lv}**: {items}")

    # 维度交叉表
    if _by_dim:
        st.markdown("**维度拆解**:")
        dim_rows = []
        for dim, scs in sorted(_by_dim.items()):
            _usable = sum(1 for s in scs if any(v is not None and v >= 1 for v in s.usable.values()))
            # 统计该维度的 issue 标签
            _dim_tags: Counter = Counter()
            for s in scs:
                _dim_tags.update(s.issue_tags or [])
            _top_tags = ", ".join(f"{t}" for t, _ in _dim_tags.most_common(3)) if _dim_tags else "-"
            dim_rows.append({"\u7ef4\u5ea6": dim, "\u7ec4\u6570": len(scs), "\u53ef\u7528": f"{_usable}/{len(scs)}", "\u9ad8\u9891 Issue": _top_tags})
        st.dataframe(dim_rows, use_container_width=True, hide_index=True)

    # 不可用清单（三档中任一档标为不可用=0）
    bad_list = [(sc, _tid_entry.get(sc.triplet_id, {})) for sc in completed if any(v == 0 for v in sc.usable.values())]
    if bad_list:
        st.markdown("**👎 不可用组清单**:")
        for sc, ent in bad_list:
            dim = ent.get("evaluation_dimension", "?")
            topic = ent.get("topic", "?")
            tags = ", ".join(sc.issue_tags) if sc.issue_tags else "-"
            _gen_note = (sc.notes or {}).get("general", sc.note) or ""
            st.markdown(f"- `{dim}` / {topic} — tags: {tags} {'| ' + _gen_note if _gen_note else ''}")

    # 评审意见汇总
    _all_notes: dict[str, list[str]] = {"general": [], "high": [], "medium": [], "low": []}
    for sc in completed:
        notes = sc.notes or {}
        ent = _tid_entry.get(sc.triplet_id, {})
        dim = ent.get("evaluation_dimension", "?")
        topic = ent.get("topic", "?")
        prefix = f"`{dim}`/{topic}"
        for key in ("general", "high", "medium", "low"):
            txt = (notes.get(key) or "").strip()
            if txt:
                _all_notes[key].append(f"{prefix}: {txt}")
    _has_any_note = any(v for v in _all_notes.values())
    if _has_any_note:
        st.markdown("**✍️ 评审意见汇总**:")
        for key, label in [("general", "✍️ 整组"), ("high", "🟢 high"), ("medium", "🟡 medium"), ("low", "🔴 low")]:
            items = _all_notes[key]
            if items:
                st.markdown(f"*{label}* ({len(items)}条):")
                for item in items:
                    st.markdown(f"- {item}")

    # LLM 总结改进方案（始终可见，不依赖是否有评审意见）
    st.markdown("---")
    st.markdown(f"**🤖 AI 改进方案总结**  `model: {_REVIEW_LLM_MODEL}`")

    # 确定缓存文件路径
    _scores_dir = st.session_state.get("_qc_scores_path", None)
    _summary_path: Path | None = None
    if _scores_dir:
        _sp = Path(_scores_dir)
        _summary_path = _sp.parent / _sp.name.replace("_scores.jsonl", "_ai_summary.md")

    # 读取已缓存内容
    _cached_summary = ""
    if _summary_path and _summary_path.exists():
        _cached_summary = _summary_path.read_text(encoding="utf-8").strip()

    if _cached_summary:
        st.markdown(_cached_summary)
        st.caption(f"📄 已从文件加载: `{_summary_path.name}`")
        if st.button("🔄 重新生成", key="regen_ai_summary"):
            _notes_text = ""
            for key, label in [("general", "整组"), ("high", "high档"), ("medium", "medium档"), ("low", "low档")]:
                items = _all_notes[key]
                if items:
                    _notes_text += f"\n[{label}]\n" + "\n".join(f"- {it}" for it in items)
            with st.spinner("正在调用 LLM 重新生成改进方案..."):
                _summary = _llm_summarize_notes(_notes_text, tag_cnt_by_level)
            if _summary_path and not _summary.startswith("⚠️"):
                _summary_path.write_text(_summary, encoding="utf-8")
                st.success(f"✅ 已保存到 `{_summary_path.name}`")
            st.markdown(_summary)
    else:
        st.info("还未生成 AI 改进方案。点击下方按钮调用 LLM 生成。")
        if st.button("🤖 生成 AI 改进方案", key="gen_ai_summary"):
            _notes_text = ""
            for key, label in [("general", "整组"), ("high", "high档"), ("medium", "medium档"), ("low", "low档")]:
                items = _all_notes[key]
                if items:
                    _notes_text += f"\n[{label}]\n" + "\n".join(f"- {it}" for it in items)
            with st.spinner("正在调用 LLM 生成改进方案..."):
                _summary = _llm_summarize_notes(_notes_text, tag_cnt_by_level)
            if _summary_path and not _summary.startswith("⚠️"):
                _summary_path.write_text(_summary, encoding="utf-8")
                st.success(f"✅ 已保存到 `{_summary_path.name}`")
            st.markdown(_summary)
            if not _summary.startswith("⚠️"):
                if st.button("🚀 查看 Prompt 演化对比", key="goto_evolution_after_gen", type="primary"):
                    st.session_state["skill_mode"] = "Prompt 演化"
                    st.rerun()


# ---------- 顶层入口 ----------


def render_qc_page(samples_path: Path, index_path: Path, scores_path: Path) -> None:
    # 从文件名推断 round/tag
    _m = re.match(r"round(\d+)(?:_([^_]+))?_samples\.jsonl$", samples_path.name)
    _round_id = int(_m.group(1)) if _m else 1
    _tag = (_m.group(2) or "") if _m else ""
    _title_suffix = f" · Round {_round_id}" + (f" ({_tag})" if _tag else "")

    # Round 切换检测：清除表单 widget 缓存，确保评估信息正确加载
    _round_key = f"{_round_id}_{_tag}"
    if st.session_state.get("_qc_current_round_key") != _round_key:
        # 清除上一轮的表单 widget keys
        _stale_keys = [k for k in list(st.session_state.keys())
                       if any(k.startswith(p) for p in ("usable_", "d2_", "d3h_", "d3m_", "d3l_", "d4h_", "d4m_", "d4l_", "tags_", "note_"))]
        for k in _stale_keys:
            del st.session_state[k]
        # 重置组选择器 & 强制重新加载 scores
        st.session_state.pop("qc_sel_idx", None)
        st.session_state.pop("qc_pending_idx", None)
        # 清除旧 scores 缓存
        _old_cache_keys = [k for k in list(st.session_state.keys()) if k.startswith("qc_scores_")]
        for k in _old_cache_keys:
            del st.session_state[k]
        st.session_state["_qc_current_round_key"] = _round_key

    st.title(f"✏️ QC 评估{_title_suffix}")

    # 数据准备
    if not samples_path.exists() or not index_path.exists():
        st.error(
            f"❌ 缺少数据文件：\n- {samples_path}\n- {index_path}\n"
            "请先运行 `python scripts/sample_qc.py` 生成抽样集。"
        )
        return

    index = load_index(index_path)
    samples = load_samples(samples_path)
    triplets = samples_by_triplet(index, samples)

    # ========== 维度数据量 & 抽样信息 ==========
    # 尝试加载源数据统计
    _project_root = samples_path.resolve().parents[2]  # data/qc/xxx -> data -> project root
    _tag_suffix = f"_{_tag}" if _tag else ""
    _source_path = _project_root / "data" / "samples" / "value_qa" / f"verified.round{_round_id}{_tag_suffix}_raw.jsonl"
    _source_stats: dict[str, Any] = {}
    if _source_path.exists():
        _src_lines = [json.loads(l) for l in _source_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        _by_dim_src: dict[str, int] = Counter()
        _by_pm_src: dict[str, int] = Counter()
        _by_dim_groups: dict[str, set] = {}
        for s in _src_lines:
            meta = s.get("metadata") or {}
            dim = meta.get("evaluation_dimension", "?")
            _by_dim_src[dim] += 1
            _by_pm_src[meta.get("primary_metric", "?")] += 1
            # 用 fact 聚合组数
            fact = meta.get("fact", "")
            _by_dim_groups.setdefault(dim, set()).add(fact)
        _source_stats = {
            "total_samples": len(_src_lines),
            "total_groups": len(_src_lines) // 3,
            "by_dimension": dict(_by_dim_src.most_common()),
            "by_dimension_groups": {k: len(v) for k, v in _by_dim_groups.items()},
            "by_primary_metric": dict(_by_pm_src.most_common()),
        }

    # 抽样统计（从 index 派生）
    _sampled_by_dim: dict[str, int] = Counter()
    _sampled_by_pm: dict[str, int] = Counter()
    for entry in index:
        _sampled_by_dim[entry.get("evaluation_dimension", "?")] += 1
        _sampled_by_pm[entry.get("primary_metric", "?")] += 1

    with st.expander("📊 维度数据量 & 抽样信息", expanded=True):
        if _source_stats:
            st.markdown(
                f"源数据: **{_source_stats['total_samples']}** 条 / **{_source_stats['total_groups']}** 组三联组 "
                f"→ 抽样: **{len(index)}** 组 = **{len(index) * 3}** 条"
            )
        else:
            st.markdown(f"抽样: **{len(index)}** 组 = **{len(index) * 3}** 条")

        # 一二级维度合并表（同一一级维度的行放在一起）
        _dim_rows = []
        _all_dims = sorted(set(list(_sampled_by_dim.keys()) + list((_source_stats.get("by_dimension") or {}).keys())))
        # 建立二级维度 → 一级维度的映射（从 index 获取）
        _dim_to_pm: dict[str, str] = {}
        for entry in index:
            _dim_to_pm[entry.get("evaluation_dimension", "?")] = entry.get("primary_metric", "?")
        for dim in _all_dims:
            pm = _dim_to_pm.get(dim, "?")
            src_cnt = _source_stats.get("by_dimension", {}).get(dim, "-") if _source_stats else "-"
            src_groups = _source_stats.get("by_dimension_groups", {}).get(dim, "-") if _source_stats else "-"
            sampled = _sampled_by_dim.get(dim, 0)
            _dim_rows.append({"_pm_sort": pm, "一级维度": pm, "二级维度": dim, "源数据量": src_cnt, "源数据组数": src_groups, "抽样组数": sampled})
        # 按一级维度分组排列
        _dim_rows.sort(key=lambda r: r["_pm_sort"])
        for r in _dim_rows:
            del r["_pm_sort"]
        st.dataframe(_dim_rows, use_container_width=True, hide_index=True)

    st.divider()

    # session 缓存 scores（避免每次 rerender 重读）
    cache_key = f"qc_scores_{scores_path}"
    if cache_key not in st.session_state:
        st.session_state[cache_key] = load_scores(scores_path)
    scores: dict[str, TripletScore] = st.session_state[cache_key]
    # 记录 scores_path 供报告缓存使用
    st.session_state["_qc_scores_path"] = str(scores_path)

    # 顶部 reviewer 输入
    reviewer = st.sidebar.text_input("👤 评估员", value=st.session_state.get("qc_reviewer", "shiyewang"), key="qc_reviewer")

    # 进度面板
    progress = compute_progress(index, scores)
    pct = progress.completed / progress.total_triplets if progress.total_triplets else 0.0
    st.progress(pct, text=f"已评 {progress.completed} / {progress.total_triplets} 三联组（{pct * 100:.0f}%）")
    if progress.completed > 0:
        # 可用率（按档位统计）
        completed_scores = [sc for sc in scores.values() if sc.is_complete]
        nc = len(completed_scores)
        total = progress.total_triplets

        st.markdown("**可用率**（👍+🤏 / 总组数，每档独立）")
        _lv_labels = [("high", "🟢 high"), ("medium", "🟡 medium"), ("low", "🔴 low")]
        u_cols = st.columns(3)
        for col, (lv, lbl) in zip(u_cols, _lv_labels):
            lv_ok = sum(1 for sc in completed_scores if sc.usable.get(lv) == 2)
            lv_mid = sum(1 for sc in completed_scores if sc.usable.get(lv) == 1)
            lv_bad = sum(1 for sc in completed_scores if sc.usable.get(lv) == 0)
            lv_unmarked = total - lv_ok - lv_mid - lv_bad
            _rated = lv_ok + lv_mid + lv_bad
            pct = f"{(lv_ok+lv_mid)/_rated*100:.0f}%" if _rated else "-"
            col.metric(f"{lbl}", f"👍{lv_ok} 🤏{lv_mid} 👎{lv_bad} ⭕{lv_unmarked} ({pct})")

        # Issue 标签频次（按档位分组）
        _tag_by_lv: dict[str, Counter] = {"high": Counter(), "medium": Counter(), "low": Counter()}
        for sc in completed_scores:
            for lv in ("high", "medium", "low"):
                _tag_by_lv[lv].update(sc.issue_tags_by_level.get(lv, []))
        _has_any_tag = any(c for c in _tag_by_lv.values())
        if _has_any_tag:
            st.markdown("**Issue 标签频次**")
            for lv, emoji in [("high", "🟢"), ("medium", "🟡"), ("low", "🔴")]:
                if _tag_by_lv[lv]:
                    _items = [f"`{t}` ×{c}" for t, c in _tag_by_lv[lv].most_common()]
                    st.markdown(f"{emoji} **{lv}**: {' | '.join(_items)}")

    # 未完成清单（明确告诉用户每个未完成组缺哪些项）
    incomplete_lines: list[str] = []
    for i, entry in enumerate(index, 1):
        tid = entry["triplet_id"]
        sc = scores.get(tid)
        miss: list[str] = []
        if sc is None:
            miss.append("整组未评")
        else:
            # 检查可用性是否已标记
            for lv in ("high", "medium", "low"):
                if sc.usable.get(lv) is None:
                    miss.append(f"可用性·{lv}")
        if miss:
            incomplete_lines.append(
                f"- **#{i}** `{entry['evaluation_dimension']}` · {entry.get('topic', '?')} → 缺：**{', '.join(miss)}**"
            )
    if incomplete_lines:
        st.error(
            "❗ 未完成清单（共 {n} 组）：\n\n{body}\n\n"
            "👉 用上方下拉跳到对应组，把这些项打分后再保存。".format(
                n=len(incomplete_lines), body="\n".join(incomplete_lines)
            )
        )
    else:
        st.success(f"🎉 全部 {len(index)} 组评估已完整！")
    st.divider()

    # 三联组选择器
    def _label(i: int, entry: dict) -> str:
        tid = entry["triplet_id"]
        done = "✅" if (tid in scores and scores[tid].is_complete) else "⬜"
        return f"#{i + 1} [{done}] {entry['evaluation_dimension']} · {entry.get('topic', '?')} · {entry.get('medium_pattern', '?')}"

    # 在 widget 实例化前消化挂起的跳转意图（避免 StreamlitAPIException）
    if "qc_pending_idx" in st.session_state:
        st.session_state["qc_sel_idx"] = st.session_state.pop("qc_pending_idx")

    sel_idx = st.selectbox(
        "选择三联组",
        list(range(len(index))),
        format_func=lambda i: _label(i, index[i]),
        key="qc_sel_idx",
    )
    entry = index[sel_idx]
    tid = entry["triplet_id"]
    triplet = triplets.get(tid, {})

    # 元信息
    st.markdown(
        f"**维度**: `{entry['evaluation_dimension']}` （{entry.get('primary_metric', '?')}） · "
        f"**topic**: `{entry.get('topic', '?')}` · "
        f"**medium_pattern**: `{entry.get('medium_pattern', '-')}`"
    )

    # 源文档 / fact / evidence 展示
    _any_sample = triplet.get("high") or triplet.get("medium") or triplet.get("low") or {}
    _doc_title, _doc_url, _doc_content = _get_source_doc(_any_sample)
    with st.expander("📄 源文档 & Fact & Evidence", expanded=False):
        st.markdown(f"**Fact**（出题锚点）: {entry.get('fact', '')}")
        st.markdown("---")
        _ev_text = ""
        _ev_list = _any_sample.get("evidence") or []
        if _ev_list:
            _ev_text = _ev_list[0].get("text", "")
        st.markdown(f"**Evidence**（支撑引文）: {_ev_text or '(空)'}")
        st.markdown("---")
        if _doc_title:
            st.markdown(f"**源文档**: [{_doc_title}]({_doc_url})" if _doc_url else f"**源文档**: {_doc_title}")
        if _doc_content:
            st.text_area("源文档全文", value=_doc_content, height=200, disabled=True, key=f"src_doc_{sel_idx}")

    # 三档样本
    col_h, col_m, col_l = st.columns(3)
    with col_h:
        _render_level_card("high", triplet.get("high", {}))
    with col_m:
        _render_level_card("medium", triplet.get("medium", {}))
    with col_l:
        _render_level_card("low", triplet.get("low", {}))

    st.divider()

    # 评估表单
    cur_score = scores.get(tid) or TripletScore(triplet_id=tid, reviewer=reviewer)
    cur_score.reviewer = reviewer
    new_score = _render_score_form(cur_score, sel_idx, round_prefix=_round_key)

    # 保存按钮 + 导航
    cs1, cs2, cs3, cs4 = st.columns([1, 1, 1, 3])
    with cs1:
        if st.button("⏮ 上一组", key=f"prev_{sel_idx}"):
            upsert_score(scores, new_score)
            save_scores(scores_path, scores)
            prev_idx = (sel_idx - 1) % len(index)
            st.session_state["qc_pending_idx"] = prev_idx
            st.rerun()
    with cs2:
        if st.button("💾 保存本组评估", type="primary", key=f"save_{sel_idx}"):
            upsert_score(scores, new_score)
            save_scores(scores_path, scores)
            st.success(f"✅ 已保存 → {scores_path}")
            st.rerun()
    with cs3:
        if st.button("⏭ 保存并跳转到下一组", key=f"save_next_{sel_idx}"):
            upsert_score(scores, new_score)
            save_scores(scores_path, scores)
            nxt = (sel_idx + 1) % len(index)
            st.session_state["qc_pending_idx"] = nxt
            st.rerun()
    with cs4:
        if cur_score.ts:
            st.caption(f"上次保存: {cur_score.ts}  reviewer={cur_score.reviewer}")

    st.divider()

    # 📊 整体评估报告（按钮触发，用 session_state 避免嵌套反模式）
    _rpt_flag = "_show_overall_report"
    if st.button("📊 生成整体评估报告", key="gen_overall_report_btn"):
        st.session_state[_rpt_flag] = True

    if st.session_state.get(_rpt_flag, False):
        _generate_inline_report(index, scores)
