#!/usr/bin/env python3
"""value_qa 漏标检测离线脚本（方案 B：taxonomy 词典匹配）。

扫描存量 verified.jsonl，对每条样本检测"answer 中出现价值词却未被 annotation
覆盖"的疑似漏标情况，输出统计报告（不修改原数据、不影响生成管线）。

用法：
  PYTHONPATH=. python scripts/detect_missing_annotations.py \\
      --input data/samples/value_qa/verified.jsonl \\
      [--output data/samples/value_qa/_missing_report.jsonl] \\
      [--top 20]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark.agents.skills.value_qa.missing_annotation import summarize_missing  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="value_qa 漏标检测（词典匹配）")
    parser.add_argument("--input", required=True, help="待扫描的 verified.jsonl 路径")
    parser.add_argument("--output", default="", help="可选：疑似漏标明细写入此 jsonl")
    parser.add_argument("--top", type=int, default=20, help="打印高频漏标价值词 Top N")
    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"[ERROR] 输入文件不存在: {in_path}")
        sys.exit(1)

    total = 0
    samples_with_missing = 0
    total_missing = 0
    label_counter: Counter = Counter()
    dim_counter: Counter = Counter()  # (evaluation_dimension) -> 疑似漏标样本数
    level_counter: Counter = Counter()  # value_level -> 疑似漏标样本数
    detail_rows: list[dict] = []

    for line in in_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            sample = json.loads(line)
        except json.JSONDecodeError:
            continue
        total += 1
        result = summarize_missing(sample)
        missing = result["missing"]
        if missing:
            samples_with_missing += 1
            total_missing += len(missing)
            for m in missing:
                label_counter[m["label"]] += 1
            dim_counter[result["evaluation_dimension"]] += 1
            level_counter[result["value_level"]] += 1
            detail_rows.append(result)

    # ---- 报告 ----
    print("=" * 60)
    print("value_qa 漏标检测报告（方案 B · taxonomy 词典匹配）")
    print("=" * 60)
    print(f"总样本数:            {total}")
    print(f"疑似漏标样本数:      {samples_with_missing} ({samples_with_missing/total*100:.1f}%)" if total else "无样本")
    print(f"疑似漏标总条数:      {total_missing}")
    print()

    print(f"高频漏标价值词 Top {args.top}:")
    for label, cnt in label_counter.most_common(args.top):
        print(f"  {label:12s}: {cnt}")
    print()

    print("按二级维度分布（疑似漏标样本数）:")
    for dim, cnt in dim_counter.most_common():
        print(f"  {dim:16s}: {cnt}")
    print()

    print("按档位分布（疑似漏标样本数）:")
    for lv in ["high", "medium", "low"]:
        print(f"  {lv:8s}: {level_counter.get(lv, 0)}")
    print()

    # ---- 可选明细落盘 ----
    if args.output:
        out_path = Path(args.output)
        with out_path.open("w", encoding="utf-8") as f:
            for row in detail_rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"疑似漏标明细已写入: {out_path}（{len(detail_rows)} 条）")

    print("=" * 60)
    print("说明：本报告为软信号，仅供人工复核；不修改原数据、不拒绝样本。")


if __name__ == "__main__":
    main()
