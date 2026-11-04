"""qc_data.py - QC 评分数据层（不依赖 benchmark 包）。

负责：
    - 加载 round1_samples.jsonl + round1_index.json
    - 加载 / 保存 round1_scores.jsonl（每条 = 一个三联组的评分记录）
    - 计算评分进度统计
"""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ============================================================
# Schema
# ============================================================


@dataclass
class TripletScore:
    """一个三联组的评分。"""

    triplet_id: str
    # D1 已废弃（保留字段兼容旧数据）
    d1_fact_judgable: int = 0       # 1-3，0=未评（不再作为必填）
    # D2 整组打一个分
    d2_triplet_discrim: int = 0     # 1-3，0=未评
    # D3/D4 三档分开
    d3_answer_fullness: dict[str, int] = field(default_factory=lambda: {"high": 0, "medium": 0, "low": 0})
    d4_annotation_correct: dict[str, int] = field(default_factory=lambda: {"high": 0, "medium": 0, "low": 0})
    # 可用标记（按档位）: None=未标, 2=可用, 1=勉强可用, 0=不可用
    usable: dict[str, int | None] = field(default_factory=lambda: {"high": None, "medium": None, "low": None})
    # 自由文本
    issue_tags: list[str] = field(default_factory=list)  # 扁平合并（兼容旧数据）
    issue_tags_by_level: dict[str, list[str]] = field(default_factory=lambda: {"high": [], "medium": [], "low": []})
    note: str = ""  # 整组备注（兼容旧数据）
    notes: dict[str, str] = field(default_factory=lambda: {"general": "", "high": "", "medium": "", "low": ""})
    reviewer: str = ""
    ts: str = ""

    def to_jsonable(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_complete(self) -> bool:
        """三档可用性均已标记即算 complete。"""
        for lv in ("high", "medium", "low"):
            if self.usable.get(lv) is None:
                return False
        return True


# ============================================================
# IO
# ============================================================

# Issue 标签预设（多选）
# 共性标签（三档通用）
_COMMON_TAGS = [
    "answer-偏题",
    "answer-空泛",
    "answer-套话",
    "annotation-文本不在答案中",
    "annotation-太长",
    "annotation-polarity 错",
    "annotation-缺失",
    "evidence-不来自原文",
    "fact-纯客观",
    "其它",
]

# 各档专属标签
ISSUE_TAGS_HIGH = [
    "立场不鲜明/正面不够强",
    "与 medium 区分度不足",
    "过度引申超出原文",
] + _COMMON_TAGS

ISSUE_TAGS_MEDIUM = [
    "不是灰区/偏向 high",
    "不是灰区/偏向 low",
    "medium_pattern 不准确",
    "瑕疵点不可感知",
] + _COMMON_TAGS

ISSUE_TAGS_LOW = [
    "立场不鲜明/反面不够强",
    "与 medium 区分度不足",
    "违背程度不够明显",
] + _COMMON_TAGS

# 兼容旧代码：保留合并后的完整列表
ISSUE_TAGS = list(dict.fromkeys(ISSUE_TAGS_HIGH + ISSUE_TAGS_MEDIUM + ISSUE_TAGS_LOW))


def load_index(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def load_samples(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def samples_by_triplet(index: list[dict[str, Any]], samples: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """index + samples → {triplet_id: {"high": {...}, "medium": {...}, "low": {...}}}"""
    by_id = {s.get("sample_id"): s for s in samples}
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for entry in index:
        tid = entry["triplet_id"]
        out[tid] = {
            "high": by_id.get(entry["high_id"], {}),
            "medium": by_id.get(entry["medium_id"], {}),
            "low": by_id.get(entry["low_id"], {}),
        }
    return out


# 将存储的 issue_tags 按档位解析（兼容旧数据）
_HIGH_ONLY = set(ISSUE_TAGS_HIGH) - set(_COMMON_TAGS)
_MED_ONLY = set(ISSUE_TAGS_MEDIUM) - set(_COMMON_TAGS)
_LOW_ONLY = set(ISSUE_TAGS_LOW) - set(_COMMON_TAGS)


def _parse_tags_by_level(d: dict) -> dict[str, list[str]]:
    """从 JSONL 记录解析 issue_tags_by_level。

    新格式：直接有 issue_tags_by_level 字段。
    旧格式：只有扁平 issue_tags，根据标签定义归属推断。
    """
    by_level = d.get("issue_tags_by_level")
    if isinstance(by_level, dict):
        return {"high": by_level.get("high", []), "medium": by_level.get("medium", []), "low": by_level.get("low", [])}
    # 旧数据迁移：尽力根据标签定义推断所属档位
    flat = d.get("issue_tags") or []
    result: dict[str, list[str]] = {"high": [], "medium": [], "low": []}
    for tag in flat:
        if tag in _HIGH_ONLY:
            result["high"].append(tag)
        elif tag in _MED_ONLY:
            result["medium"].append(tag)
        elif tag in _LOW_ONLY:
            result["low"].append(tag)
        else:
            # 共性标签无法确定来源，分配到各档（尽力展示）
            result["high"].append(tag)
            result["medium"].append(tag)
            result["low"].append(tag)
    return result


def load_scores(path: Path) -> dict[str, TripletScore]:
    if not path.exists():
        return {}
    out: dict[str, TripletScore] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        _usable_raw = d.get("usable")
        # 兼容旧数据：旧格式 usable 是 bool/None 或 dict[str, bool|int|None]
        if isinstance(_usable_raw, dict):
            _usable = {}
            for lv in ("high", "medium", "low"):
                v = _usable_raw.get(lv)
                if v is None:
                    _usable[lv] = None
                elif isinstance(v, int):
                    _usable[lv] = v  # 新格式: 0/1/2
                else:
                    _usable[lv] = 2 if v else 0  # 旧 bool -> 2(可用)/0(不可用)
        elif _usable_raw is not None:
            # 最旧格式：整组 bool 值 -> 三档共享
            _old_val = 2 if bool(_usable_raw) else 0
            _usable = {"high": _old_val, "medium": _old_val, "low": _old_val}
        else:
            _usable = {"high": None, "medium": None, "low": None}
        out[d["triplet_id"]] = TripletScore(
            triplet_id=d["triplet_id"],
            d1_fact_judgable=d.get("d1_fact_judgable", 0),
            d2_triplet_discrim=d.get("d2_triplet_discrim", 0),
            d3_answer_fullness=d.get("d3_answer_fullness") or {"high": 0, "medium": 0, "low": 0},
            d4_annotation_correct=d.get("d4_annotation_correct") or {"high": 0, "medium": 0, "low": 0},
            usable=_usable,
            issue_tags=d.get("issue_tags") or [],
            issue_tags_by_level=_parse_tags_by_level(d),
            note=d.get("note", ""),
            notes=d.get("notes") or {"general": d.get("note", ""), "high": "", "medium": "", "low": ""},
            reviewer=d.get("reviewer", ""),
            ts=d.get("ts", ""),
        )
    return out


def save_scores(path: Path, scores: dict[str, TripletScore]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for sc in scores.values():
            f.write(json.dumps(sc.to_jsonable(), ensure_ascii=False) + "\n")


def upsert_score(scores: dict[str, TripletScore], score: TripletScore) -> None:
    score.ts = _dt.datetime.now().isoformat(timespec="seconds")
    scores[score.triplet_id] = score


# ============================================================
# 进度
# ============================================================


@dataclass
class ProgressStats:
    total_triplets: int = 0
    completed: int = 0
    avg_d1: float = 0.0
    avg_d2: float = 0.0
    avg_d3: float = 0.0
    avg_d4: float = 0.0
    usable_count: int = 0


def compute_progress(index: list[dict[str, Any]], scores: dict[str, TripletScore]) -> ProgressStats:
    total = len(index)
    completed_scores = [sc for sc in scores.values() if sc.is_complete]
    n = len(completed_scores)
    if n == 0:
        return ProgressStats(total_triplets=total)
    avg_d1 = sum(sc.d1_fact_judgable for sc in completed_scores) / n
    avg_d2 = sum(sc.d2_triplet_discrim for sc in completed_scores) / n
    d3_vals = [v for sc in completed_scores for v in sc.d3_answer_fullness.values() if v > 0]
    d4_vals = [v for sc in completed_scores for v in sc.d4_annotation_correct.values() if v > 0]
    # usable 统计（三档中有任一档 >=1 即算可用）
    usable_cnt = sum(1 for sc in completed_scores if any(v is not None and v >= 1 for v in sc.usable.values()))
    return ProgressStats(
        total_triplets=total,
        completed=n,
        avg_d1=avg_d1,
        avg_d2=avg_d2,
        avg_d3=sum(d3_vals) / len(d3_vals) if d3_vals else 0.0,
        avg_d4=sum(d4_vals) / len(d4_vals) if d4_vals else 0.0,
        usable_count=usable_cnt,
    )
