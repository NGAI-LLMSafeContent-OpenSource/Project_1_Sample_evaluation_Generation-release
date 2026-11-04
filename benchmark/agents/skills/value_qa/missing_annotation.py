"""value_qa 漏标检测（方案 B：taxonomy 词典匹配）。

核心思路：扫描 answer 文本中出现的 taxonomy 价值词（label），若某价值词在
answer 中出现、却没有任何作用于 answer 的 value_annotation 区间覆盖它，则视为
"疑似漏标"。

设计为纯函数、无副作用，输入为基础类型（answer 字符串 + annotation 简单列表 +
维度英文 id），因此既可被离线批量脚本调用，也可被生成管线（skill._build_sample）
复用，向 metadata 写入软信号。

已知局限（方案 B）：taxonomy 的 label 多为抽象价值词（诚信/公正/爱国），answer
中的价值表达常是具体行为描述，字面命中率有限；本模块只负责抓"确凿命中词典词却
未标注"的高置信漏标，不追求全召回。
"""

from __future__ import annotations

from typing import Any

from benchmark.agents.skills.value_qa.taxonomy import anchors_for_dimension, load_taxonomy

# metadata.value_dimension 的合法英文 id
_VALID_DIMENSIONS = ("cultural_fidelity", "ethics_compliance")


def _dimension_labels(dimension: str, taxonomy: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """返回该维度下所有 taxonomy 价值词条目：[{label, key, layer}, ...]。"""
    if dimension not in _VALID_DIMENSIONS:
        return []
    tax = taxonomy or load_taxonomy()
    anchors = anchors_for_dimension(tax, dimension)
    out: list[dict[str, str]] = []
    for a in anchors:
        label = (a.get("label") or "").strip()
        if label:
            out.append({"label": label, "key": a.get("key", ""), "layer": a.get("layer", "")})
    return out


def _covered_texts(annotations: list[dict[str, Any]]) -> list[str]:
    """提取作用于 answer 的已标注 text 列表。

    注：不依赖 start/end 偏移（存量数据中该偏移可能与 answer 实际位置错位），
    改用 annotation.text 的子串包含关系判定覆盖，更稳健。
    """
    texts: list[str] = []
    for ann in annotations or []:
        if ann.get("field") == "answer":
            t = (ann.get("text") or "").strip()
            if t:
                texts.append(t)
    return texts


def _is_covered(label: str, covered_texts: list[str]) -> bool:
    """判断 label 是否被任一已标注 text 覆盖（label 为某 text 的子串）。"""
    for t in covered_texts:
        if label in t:
            return True
    return False


def detect_missing_annotations(
    answer: str,
    annotations: list[dict[str, Any]],
    dimension: str,
    *,
    taxonomy: dict[str, Any] | None = None,
    context_window: int = 10,
) -> list[dict[str, Any]]:
    """检测 answer 中疑似漏标的价值词。

    Args:
        answer: 答案文本。
        annotations: value_annotations 列表（dict 形式，含 field/text/start/end）。
        dimension: 维度英文 id（cultural_fidelity / ethics_compliance）。
        taxonomy: 可选，预加载的 taxonomy；缺省时惰性加载（带缓存）。
        context_window: 输出上下文时价值词前后截取的字符数。

    Returns:
        疑似漏标列表，每项：
        {label, value_key, layer, start, end, context}
        （同一 label 多次出现只报未被覆盖的那些位置；同 label 去重保留首个未覆盖位置）
    """
    if not answer or dimension not in _VALID_DIMENSIONS:
        return []

    labels = _dimension_labels(dimension, taxonomy)
    if not labels:
        return []

    spans = _covered_texts(annotations)
    results: list[dict[str, Any]] = []
    seen_labels: set[str] = set()

    for entry in labels:
        label = entry["label"]
        if label in seen_labels:
            continue
        # label 在 answer 中出现，且未被任一已标注 text 覆盖 → 疑似漏标
        idx = answer.find(label)
        if idx != -1 and not _is_covered(label, spans):
            pos_end = idx + len(label)
            ctx_start = max(0, idx - context_window)
            ctx_end = min(len(answer), pos_end + context_window)
            results.append(
                {
                    "label": label,
                    "value_key": entry["key"],
                    "layer": entry["layer"],
                    "start": idx,
                    "end": pos_end,
                    "context": answer[ctx_start:ctx_end],
                }
            )
            seen_labels.add(label)

    return results


def summarize_missing(sample: dict[str, Any]) -> dict[str, Any]:
    """对单条 verified.jsonl 样本（dict）做漏标检测，返回结构化结果。

    便于离线脚本直接调用；从样本中提取 answer / annotations / dimension。
    """
    meta = sample.get("metadata", {}) or {}
    dimension = meta.get("value_dimension", "")

    answer = ""
    for art in (sample.get("output", {}) or {}).get("artifacts", []) or []:
        if art.get("key") == "y":
            answer = str(art.get("value") or "")
            break

    annotations = sample.get("value_annotations", []) or []
    missing = detect_missing_annotations(answer, annotations, dimension)
    return {
        "sample_id": sample.get("sample_id", ""),
        "dimension": dimension,
        "value_level": meta.get("value_level", ""),
        "evaluation_dimension": meta.get("evaluation_dimension", ""),
        "annotation_count": len(annotations),
        "missing": missing,
    }
