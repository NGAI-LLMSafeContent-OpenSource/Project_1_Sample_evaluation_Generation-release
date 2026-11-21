"""qc_report.py - 把 QC 评分文件聚合成报告。

输出：
    1. 终端摘要（默认）
    2. data/qc/round{N}_{tag}_report.md  人类可读报告
    3. data/qc/round{N}_{tag}_summary.json  机器可读摘要

聚合维度：
    - 概览（总数 / 完整率 / 全局 D1-D4 均值 / 通过率）
    - evaluation_dimension × {D1, D2, D3, D4} 矩阵
    - medium_pattern × {D2, D3·medium} 矩阵
    - issue_tags 频次榜
    - low-score 清单（按 D1+D2+avg(D3) 排序，含 issue_tags 和 note）
    - prompt 改进建议（基于热门 issue_tags 自动生成清单）

用法：
    python scripts/qc_report.py --round 2 --tag gpt55
    python scripts/qc_report.py --round 2 --tag gpt55 --top 5
    python scripts/qc_report.py --no-write
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
QC_DIR = PROJECT_ROOT / "data" / "qc"

# 路径将在 main() 中由 configure_paths 设置
SCORES_PATH: Path = QC_DIR / "round1_scores.jsonl"
INDEX_PATH: Path = QC_DIR / "round1_index.json"
REPORT_MD_PATH: Path = QC_DIR / "round1_report.md"
SUMMARY_JSON_PATH: Path = QC_DIR / "round1_summary.json"


def configure_paths(round_id: int, tag: str = "") -> None:
    global SCORES_PATH, INDEX_PATH, REPORT_MD_PATH, SUMMARY_JSON_PATH
    suffix = f"_{tag}" if tag else ""
    SCORES_PATH = QC_DIR / f"round{round_id}{suffix}_scores.jsonl"
    INDEX_PATH = QC_DIR / f"round{round_id}{suffix}_index.json"
    REPORT_MD_PATH = QC_DIR / f"round{round_id}{suffix}_report.md"
    SUMMARY_JSON_PATH = QC_DIR / f"round{round_id}{suffix}_summary.json"

# ============================================================
# 加载
# ============================================================


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def is_complete(s: dict) -> bool:
    if s.get("d2_triplet_discrim", 0) < 1:
        return False
    d3 = s.get("d3_answer_fullness", {}) or {}
    return all(d3.get(lv, 0) >= 1 for lv in ("high", "medium", "low"))


def avg_d3(s: dict) -> float:
    vs = [v for v in (s.get("d3_answer_fullness") or {}).values() if v > 0]
    return mean(vs) if vs else 0.0


def avg_d4(s: dict) -> float:
    vs = [v for v in (s.get("d4_annotation_correct") or {}).values() if v > 0]
    return mean(vs) if vs else 0.0


def composite(s: dict) -> float:
    """D2 + avg(D3) + 0.5 * avg(D4)。"""
    return s.get("d2_triplet_discrim", 0) + avg_d3(s) + 0.5 * avg_d4(s)


# ============================================================
# 聚合函数
# ============================================================


def _level_pass(s: dict, level: str) -> bool:
    """level 通过：D3·{level} ≥ 2 且 D4·{level} ≥ 2。"""
    d3 = (s.get("d3_answer_fullness") or {}).get(level, 0)
    d4 = (s.get("d4_annotation_correct") or {}).get(level, 0)
    return d3 >= 2 and d4 >= 2


def _group_pass(s: dict) -> bool:
    """整组通过：D2≥2 且 high/medium/low 各档均通过。"""
    if s.get("d2_triplet_discrim", 0) < 2:
        return False
    return all(_level_pass(s, lv) for lv in ("high", "medium", "low"))


def _usable_lv(s: dict, level: str) -> bool:
    """某档是否标记为可用(2)（兼容旧 bool 格式）。"""
    u = s.get("usable")
    if isinstance(u, dict):
        v = u.get(level)
        if isinstance(v, int):
            return v == 2
        return v is True  # 旧 bool
    return u is True


def _marginal_lv(s: dict, level: str) -> bool:
    """某档是否标记为勉强可用(1)。"""
    u = s.get("usable")
    if isinstance(u, dict):
        return u.get(level) == 1
    return False


def _unusable_lv(s: dict, level: str) -> bool:
    """某档是否标记为不可用(0)（兼容旧 bool 格式）。"""
    u = s.get("usable")
    if isinstance(u, dict):
        v = u.get(level)
        if isinstance(v, int):
            return v == 0
        return v is False  # 旧 bool
    return u is False


def overview(scores: list[dict]) -> dict:
    n = len(scores)
    complete = [s for s in scores if is_complete(s)]
    nc = len(complete)
    if nc == 0:
        return {"total": n, "complete": 0}
    # 通过率
    high_pass = sum(1 for s in complete if _level_pass(s, "high"))
    medium_pass = sum(1 for s in complete if _level_pass(s, "medium"))
    low_pass = sum(1 for s in complete if _level_pass(s, "low"))
    group_pass = sum(1 for s in complete if _group_pass(s))
    return {
        "total": n,
        "complete": nc,
        "complete_rate": round(nc / n, 3),
        "avg_d2": round(mean(s["d2_triplet_discrim"] for s in complete), 2),
        "avg_d3": round(mean(avg_d3(s) for s in complete), 2),
        "avg_d4": round(mean(avg_d4(s) for s in complete), 2),
        "usable_high": sum(1 for s in complete if _usable_lv(s, "high")),
        "usable_medium": sum(1 for s in complete if _usable_lv(s, "medium")),
        "usable_low": sum(1 for s in complete if _usable_lv(s, "low")),
        "marginal_high": sum(1 for s in complete if _marginal_lv(s, "high")),
        "marginal_medium": sum(1 for s in complete if _marginal_lv(s, "medium")),
        "marginal_low": sum(1 for s in complete if _marginal_lv(s, "low")),
        "unusable_high": sum(1 for s in complete if _unusable_lv(s, "high")),
        "unusable_medium": sum(1 for s in complete if _unusable_lv(s, "medium")),
        "unusable_low": sum(1 for s in complete if _unusable_lv(s, "low")),
        "usable_rate_high": round(sum(1 for s in complete if _usable_lv(s, "high") or _marginal_lv(s, "high")) / nc, 3),
        "usable_rate_medium": round(sum(1 for s in complete if _usable_lv(s, "medium") or _marginal_lv(s, "medium")) / nc, 3),
        "usable_rate_low": round(sum(1 for s in complete if _usable_lv(s, "low") or _marginal_lv(s, "low")) / nc, 3),
        "pass_high": f"{high_pass}/{nc}",
        "pass_medium": f"{medium_pass}/{nc}",
        "pass_low": f"{low_pass}/{nc}",
        "pass_group": f"{group_pass}/{nc}",
        "pass_high_rate": round(high_pass / nc, 3),
        "pass_medium_rate": round(medium_pass / nc, 3),
        "pass_low_rate": round(low_pass / nc, 3),
        "pass_group_rate": round(group_pass / nc, 3),
    }


def by_dimension(scores: list[dict], index: list[dict]) -> dict[str, dict]:
    """每个 evaluation_dimension 的 D2-D4 均值。"""
    dim_of = {e["triplet_id"]: e["evaluation_dimension"] for e in index}
    bucket: dict[str, list[dict]] = defaultdict(list)
    for s in scores:
        if is_complete(s):
            bucket[dim_of.get(s["triplet_id"], "?")].append(s)
    out = {}
    for dim, items in bucket.items():
        out[dim] = {
            "n": len(items),
            "d2": round(mean(s["d2_triplet_discrim"] for s in items), 2),
            "d3": round(mean(avg_d3(s) for s in items), 2),
            "d4": round(mean(avg_d4(s) for s in items), 2),
        }
    return out


def by_medium_pattern(scores: list[dict], index: list[dict]) -> dict[str, dict]:
    """每个 medium_pattern 的 D2 / D3·medium 均值。"""
    pat_of = {e["triplet_id"]: e.get("medium_pattern", "-") for e in index}
    bucket: dict[str, list[dict]] = defaultdict(list)
    for s in scores:
        if is_complete(s):
            bucket[pat_of.get(s["triplet_id"], "-")].append(s)
    out = {}
    for pat, items in bucket.items():
        d3_med = [s["d3_answer_fullness"]["medium"] for s in items]
        out[pat] = {
            "n": len(items),
            "d2": round(mean(s["d2_triplet_discrim"] for s in items), 2),
            "d3_medium": round(mean(d3_med), 2) if d3_med else 0.0,
        }
    return out


def issue_tag_freq(scores: list[dict]) -> list[tuple[str, int]]:
    cnt: Counter = Counter()
    for s in scores:
        cnt.update(s.get("issue_tags") or [])
    return cnt.most_common()


def low_score_list(scores: list[dict], index: list[dict], top: int) -> list[dict]:
    """按 composite 升序，列出最低 N 条。"""
    by_id = {e["triplet_id"]: e for e in index}
    enriched = []
    for s in scores:
        if not is_complete(s):
            continue
        e = by_id.get(s["triplet_id"], {})
        enriched.append(
            {
                "triplet_id": s["triplet_id"],
                "dim": e.get("evaluation_dimension", "?"),
                "topic": e.get("topic", "?"),
                "pattern": e.get("medium_pattern", "-"),
                "d2": s["d2_triplet_discrim"],
                "d3": round(avg_d3(s), 2),
                "d4": round(avg_d4(s), 2),
                "composite": round(composite(s), 2),
                "tags": s.get("issue_tags") or [],
                "note": (s.get("note") or "").strip(),
            }
        )
    enriched.sort(key=lambda x: x["composite"])
    return enriched[:top]


def notes_summary(scores: list[dict], index: list[dict]) -> dict[str, list[str]]:
    """汇总所有非空评审意见，按 general/high/medium/low 分组。"""
    by_id = {e["triplet_id"]: e for e in index}
    result: dict[str, list[str]] = {"general": [], "high": [], "medium": [], "low": []}
    for s in scores:
        if not is_complete(s):
            continue
        notes = s.get("notes") or {}
        entry = by_id.get(s["triplet_id"], {})
        dim = entry.get("evaluation_dimension", "?")
        topic = entry.get("topic", "?")
        prefix = f"{dim}/{topic}"
        for key in ("general", "high", "medium", "low"):
            txt = (notes.get(key) or "").strip()
            if txt:
                result[key].append(f"{prefix}: {txt}")
    # 兼容旧数据: 如果 notes 为空但 note 字段有内容
    for s in scores:
        if not is_complete(s):
            continue
        notes = s.get("notes") or {}
        if any((notes.get(k) or "").strip() for k in ("general", "high", "medium", "low")):
            continue
        note = (s.get("note") or "").strip()
        if note:
            entry = by_id.get(s["triplet_id"], {})
            prefix = f"{entry.get('evaluation_dimension', '?')}/{entry.get('topic', '?')}"
            result["general"].append(f"{prefix}: {note}")
    return result


# ============================================================
# Prompt 改进建议生成
# ============================================================

# tag → 推荐改 prompt 的位置（针对 ROUTE_PROMPT / TRIPLET_PROMPT）
TAG_TO_ACTION = {
    "fact-纯客观": "ROUTE_PROMPT 加约束：fact 必须含价值倾向，纯日期/数字/流程的事实直接 reject",
    "fact-含价值含义": "ROUTE_PROMPT 调整：fact 表述应保留事实陈述形态，不要把价值判断写死在 fact 里",
    "三档雷同": "TRIPLET_PROMPT 强化：在 high/medium/low 各档增加「立场对立」的硬性指令和反例",
    "high/low 立场不鲜明": "TRIPLET_PROMPT 加示例：high 必须含正向规范用词，low 必须含明显反价值/越界表达",
    "medium 不是灰区": "TRIPLET_PROMPT 重写 medium_pattern 三类的判定锚点（letter/partial/hedged 各给 1 个 anti-example）",
    "answer-套话": "TRIPLET_PROMPT 加约束：每档答案必须引用 evidence 中的具体名词/事件/数据",
    "answer-空泛": "TRIPLET_PROMPT 加示例：每档答案至少含 1 个事实细节或场景描写",
    "answer-偏题": "TRIPLET_PROMPT 加自检：答案末尾追加「与 question 的关联是 …」一句话内审",
    "annotation-text 不是子串": "verifier 加硬校验：annotation.text 必须是 answer 子串，否则整组 reject",
    "annotation-polarity 错": "TRIPLET_PROMPT 在 annotation block 显式给出 polarity 选择规则（high→positive, low→negative）",
    "annotation-缺失": "TRIPLET_PROMPT 强制：每档至少 1 条 annotation；verifier 兜底校验",
    "evidence-不来自原文": "TRIPLET_PROMPT 加约束：所有 answer 中的事实必须能在 evidence 段落里找到",
    "其它": "查看具体 note，case-by-case 处理",
}


def prompt_improvements(tag_freq: list[tuple[str, int]]) -> list[dict]:
    out = []
    for tag, cnt in tag_freq:
        action = TAG_TO_ACTION.get(tag, "（无对应建议，请人工判断）")
        out.append({"tag": tag, "count": cnt, "action": action})
    return out


# ============================================================
# 渲染
# ============================================================


def render_md(report: dict) -> str:
    o = report["overview"]
    title = report.get("title", "QC Round 1 报告")
    lines = []
    lines.append(f"# {title}\n")
    lines.append(f"- 评分总组数: **{o.get('total', 0)}**")
    lines.append(f"- 完整组数: **{o.get('complete', 0)}**（{o.get('complete_rate', 0) * 100:.0f}%）")
    if "avg_d2" in o:
        lines.append(
            f"- 全局均值: D2={o['avg_d2']} · D3={o['avg_d3']} · D4={o['avg_d4']}"
        )
    if "usable_rate_high" in o:
        lines.append(
            f"- 可用率: "
            f"🟢 high=👍{o['usable_high']}/🤏{o.get('marginal_high',0)}/👎{o['unusable_high']}({o['usable_rate_high']*100:.0f}%) "
            f"| 🟡 medium=👍{o['usable_medium']}/🤏{o.get('marginal_medium',0)}/👎{o['unusable_medium']}({o['usable_rate_medium']*100:.0f}%) "
            f"| 🔴 low=👍{o['usable_low']}/🤏{o.get('marginal_low',0)}/👎{o['unusable_low']}({o['usable_rate_low']*100:.0f}%)"
        )
    if "pass_group" in o:
        lines.append(f"- 通过率: 整组={o['pass_group']}({o['pass_group_rate']*100:.0f}%) "
                     f"| high={o['pass_high']}({o['pass_high_rate']*100:.0f}%) "
                     f"| medium={o['pass_medium']}({o['pass_medium_rate']*100:.0f}%) "
                     f"| low={o['pass_low']}({o['pass_low_rate']*100:.0f}%)")
    lines.append("")

    # 维度交叉
    lines.append("## 1. 维度交叉（evaluation_dimension × D2-D4）\n")
    lines.append("| 维度 | n | D2 | D3 | D4 |")
    lines.append("|---|---:|---:|---:|---:|")
    for dim, m in sorted(report["by_dimension"].items(), key=lambda kv: -kv[1]["n"]):
        lines.append(f"| {dim} | {m['n']} | {m['d2']} | {m['d3']} | {m['d4']} |")
    lines.append("")

    # medium_pattern 交叉
    lines.append("## 2. medium_pattern 交叉\n")
    lines.append("| pattern | n | D2 (整组区分度) | D3·medium |")
    lines.append("|---|---:|---:|---:|")
    for pat, m in sorted(report["by_medium_pattern"].items(), key=lambda kv: -kv[1]["n"]):
        lines.append(f"| {pat} | {m['n']} | {m['d2']} | {m['d3_medium']} |")
    lines.append("")

    # issue 标签
    lines.append("## 3. Issue 标签频次\n")
    if report["issue_tag_freq"]:
        lines.append("| 标签 | 次数 |")
        lines.append("|---|---:|")
        for tag, cnt in report["issue_tag_freq"]:
            lines.append(f"| {tag} | {cnt} |")
    else:
        lines.append("（无标签——本轮所有组都没贴 issue）")
    lines.append("")

    # 低分清单
    lines.append(f"## 4. 低分清单（Top {len(report['low_score_list'])}, 按 composite 升序）\n")
    for item in report["low_score_list"]:
        tags = " / ".join(item["tags"]) if item["tags"] else "-"
        lines.append(
            f"### {item['dim']} · {item['topic']} · `{item['pattern']}`  composite={item['composite']}"
        )
        lines.append(
            f"- D2={item['d2']} D3={item['d3']} D4={item['d4']}"
        )
        lines.append(f"- triplet_id: `{item['triplet_id']}`")
        lines.append(f"- 标签: {tags}")
        if item["note"]:
            lines.append(f"- 备注: {item['note']}")
        lines.append("")

    # prompt 改进建议
    lines.append("## 5. Prompt 改进建议（按标签热度排序）\n")
    if report["prompt_improvements"]:
        for imp in report["prompt_improvements"]:
            lines.append(f"- **[{imp['count']}×] `{imp['tag']}`** → {imp['action']}")
    else:
        lines.append("（本轮没有贴标签，无自动建议——可结合低分清单的 note 手动总结）")
    lines.append("")

    # 评审意见汇总
    ns = report.get("notes_summary") or {}
    _has_notes = any(ns.get(k) for k in ("general", "high", "medium", "low"))
    if _has_notes:
        lines.append("## 6. 评审意见汇总\n")
        for key, label in [("general", "✍️ 整组"), ("high", "🟢 high"), ("medium", "🟡 medium"), ("low", "🔴 low")]:
            items = ns.get(key, [])
            if items:
                lines.append(f"### {label} ({len(items)}条)\n")
                for item in items:
                    lines.append(f"- {item}")
                lines.append("")

    # AI 改进方案
    ai_summary = report.get("ai_improvement_summary", "")
    if ai_summary:
        lines.append(f"## 7. AI 改进方案总结 (model: {_REVIEW_LLM_MODEL})\n")
        lines.append(ai_summary)
        lines.append("")

    return "\n".join(lines)


def render_console(report: dict) -> None:
    o = report["overview"]
    title = report.get("title", "QC Round 1 报告")
    print(f"\n=== {title} ===")
    print(
        f"完整 {o.get('complete', 0)}/{o.get('total', 0)} | "
        f"D2={o.get('avg_d2', 0)} "
        f"D3={o.get('avg_d3', 0)} D4={o.get('avg_d4', 0)}"
    )
    if "usable_rate_high" in o:
        print(
            f"可用率: "
            f"🟢 high=👍{o['usable_high']}/🤏{o.get('marginal_high',0)}/👎{o['unusable_high']}({o['usable_rate_high']*100:.0f}%) "
            f"| 🟡 medium=👍{o['usable_medium']}/🤏{o.get('marginal_medium',0)}/👎{o['unusable_medium']}({o['usable_rate_medium']*100:.0f}%) "
            f"| 🔴 low=👍{o['usable_low']}/🤏{o.get('marginal_low',0)}/👎{o['unusable_low']}({o['usable_rate_low']*100:.0f}%)"
        )
    if "pass_group" in o:
        print(
            f"通过率: 整组={o['pass_group']}({o['pass_group_rate']*100:.0f}%) "
            f"| high={o['pass_high']}({o['pass_high_rate']*100:.0f}%) "
            f"| medium={o['pass_medium']}({o['pass_medium_rate']*100:.0f}%) "
            f"| low={o['pass_low']}({o['pass_low_rate']*100:.0f}%)"
        )
    print("\n[维度] dim | n | D2 D3 D4")
    for dim, m in sorted(report["by_dimension"].items(), key=lambda kv: -kv[1]["n"]):
        print(f"  {dim:<14} | {m['n']:>2} | {m['d2']} {m['d3']} {m['d4']}")
    print("\n[pattern] pat | n | D2 D3·med")
    for pat, m in sorted(report["by_medium_pattern"].items(), key=lambda kv: -kv[1]["n"]):
        print(f"  {pat:<18} | {m['n']:>2} | {m['d2']} {m['d3_medium']}")
    print("\n[Issue 标签]")
    if report["issue_tag_freq"]:
        for tag, cnt in report["issue_tag_freq"]:
            print(f"  {cnt:>2} × {tag}")
    else:
        print("  （无）")
    print(f"\n[低分清单 Top {len(report['low_score_list'])}]")
    for item in report["low_score_list"]:
        tags = " / ".join(item["tags"]) if item["tags"] else "-"
        print(
            f"  composite={item['composite']:.2f}  "
            f"{item['dim']}/{item['pattern']:<18} "
            f"D2={item['d2']} D3={item['d3']} | tags={tags}"
        )
    print("\n[Prompt 改进建议]")
    for imp in report["prompt_improvements"]:
        print(f"  [{imp['count']}×] {imp['tag']}  →  {imp['action']}")

    # 评审意见汇总
    ns = report.get("notes_summary") or {}
    _has_notes = any(ns.get(k) for k in ("general", "high", "medium", "low"))
    if _has_notes:
        print("\n[评审意见汇总]")
        for key, label in [("general", "整组"), ("high", "high"), ("medium", "medium"), ("low", "low")]:
            items = ns.get(key, [])
            if items:
                print(f"  --- {label} ({len(items)}条) ---")
                for item in items:
                    print(f"    • {item}")

    # AI 改进方案
    ai_summary = report.get("ai_improvement_summary", "")
    if ai_summary:
        print("\n[🤖 AI 改进方案总结]")
        for line in ai_summary.split("\n"):
            print(f"  {line}")


# ============================================================
# LLM 总结改进方案
# ============================================================

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


def _llm_summarize(notes_summary: dict[str, list[str]], issue_freq: list[tuple[str, int]]) -> str:
    """调用 LLM 根据评审意见生成改进方案。"""
    notes_text = ""
    for key, label in [("general", "整组"), ("high", "high档"), ("medium", "medium档"), ("low", "low档")]:
        items = notes_summary.get(key, [])
        if items:
            notes_text += f"\n[{label}]\n" + "\n".join(f"- {it}" for it in items)
    if not notes_text.strip():
        return ""
    issue_tags_text = "\n".join(f"- {tag} ×{cnt}" for tag, cnt in issue_freq) if issue_freq else "(无)"
    prompt = _REVIEW_SUMMARY_PROMPT.format(notes=notes_text, issue_tags=issue_tags_text)
    try:
        from benchmark.adapters.llm import build_llm_adapter
        adapter = build_llm_adapter(model=_REVIEW_LLM_MODEL, api_base=_REVIEW_LLM_API_BASE)
        result = asyncio.run(adapter.complete(prompt, temperature=0.3, max_tokens=1500))
        return result.strip() if result else ""
    except Exception as e:
        return f"⚠️ LLM 调用失败: {e}"


# ============================================================
# main
# ============================================================


def build_report(top: int, with_ai: bool = True) -> dict:
    scores = load_jsonl(SCORES_PATH)
    index = load_json(INDEX_PATH)
    _notes = notes_summary(scores, index)
    _issue_freq = issue_tag_freq(scores)
    report = {
        "overview": overview(scores),
        "by_dimension": by_dimension(scores, index),
        "by_medium_pattern": by_medium_pattern(scores, index),
        "issue_tag_freq": _issue_freq,
        "low_score_list": low_score_list(scores, index, top),
        "prompt_improvements": prompt_improvements(_issue_freq),
        "notes_summary": _notes,
    }
    # AI 改进方案
    if with_ai and any(_notes.get(k) for k in ("general", "high", "medium", "low")):
        print("\n🤖 正在调用 LLM 生成改进方案...")
        report["ai_improvement_summary"] = _llm_summarize(_notes, _issue_freq)
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=1, help="round id")
    ap.add_argument("--tag", type=str, default="", help="模型标识，如 gpt55")
    ap.add_argument("--top", type=int, default=5, help="低分清单条数，默认 5")
    ap.add_argument("--no-write", action="store_true", help="只打印，不落盘")
    args = ap.parse_args()

    configure_paths(args.round, args.tag)

    if not SCORES_PATH.exists():
        print(f"❌ 评分文件不存在: {SCORES_PATH}")
        return

    title_tag = f"_{args.tag}" if args.tag else ""
    report = build_report(args.top)
    report["title"] = f"QC Round {args.round}{title_tag} 报告"
    render_console(report)

    if not args.no_write:
        REPORT_MD_PATH.write_text(render_md(report), encoding="utf-8")
        SUMMARY_JSON_PATH.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n✅ Markdown 报告: {REPORT_MD_PATH.relative_to(PROJECT_ROOT)}")
        print(f"✅ JSON 摘要:    {SUMMARY_JSON_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
