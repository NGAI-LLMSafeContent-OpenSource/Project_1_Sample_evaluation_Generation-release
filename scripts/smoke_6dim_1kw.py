#!/usr/bin/env python3
"""Smoke test: 每维度 1 个 keyword（共 6 个），串行执行，详细计时。

用法: PYTHONPATH=. python -u scripts/smoke_6dim_1kw.py 2>&1 | tee logs/smoke_6dim_1kw.log
"""

import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import benchmark.bootstrap  # noqa: E402, F401

from benchmark.pipelines.sample_generation import SampleGenerationPipeline
from benchmark.schemas import SkillGenerationRequest

# ═══════════════ 每维度取第 1 个 keyword ═══════════════
SMOKE_KEYWORDS = {
    "文化元素符合度": "丝绸之路",
    "行为社会规范符合度": "孝道文化",
    "价值观忠实度": "核心价值",
    "公平合规度": "残疾人保障法",
    "伤害风险合规度": "网络暴力信息治理规定",
    "隐私合规度": "人脸识别门禁",
}

LIMIT = 12
CORPUS_DIR = Path("data/corpus/baidu")

# ── 输出文件 ──
TIMESTAMP = time.strftime("%Y%m%d_%H%M%S")
OUT_DIR = Path("data/samples/value_qa")
OUT_FILE = OUT_DIR / f"smoke_6dim_1kw_{TIMESTAMP}.jsonl"


def load_cached_docs(topic: str) -> list:
    from benchmark.schemas import SourceDocument
    exact_path = CORPUS_DIR / f"{topic}.jsonl"
    if exact_path.exists():
        docs = []
        for line in exact_path.read_text().splitlines():
            if line.strip():
                docs.append(SourceDocument.model_validate(json.loads(line)))
        if docs:
            return docs
    for p in CORPUS_DIR.glob("*.jsonl"):
        if topic in p.stem or p.stem in topic:
            docs = []
            for line in p.read_text().splitlines():
                if line.strip():
                    docs.append(SourceDocument.model_validate(json.loads(line)))
            if docs:
                return docs
    return []


async def run_one(dim_name: str, keyword: str, idx: int) -> dict:
    """单个 keyword 生成，返回计时 + 统计。"""
    print(f"\n{'='*60}")
    print(f"[{idx}/6] [{dim_name}] keyword={keyword}")
    print(f"{'='*60}", flush=True)

    timings = {}

    # 阶段 1: 文档加载
    t0 = time.time()
    cached_docs = load_cached_docs(keyword)
    timings["doc_load"] = time.time() - t0
    print(f"  [1] 文档加载: {timings['doc_load']:.1f}s  ({len(cached_docs)} docs from cache)", flush=True)

    # 阶段 2: pipeline（含事实抽取 + 三档生成 + 验证）
    t1 = time.time()
    try:
        pipe = SampleGenerationPipeline()
        req = SkillGenerationRequest(
            topic=keyword,
            skill_ids=["value_qa"],
            limit=LIMIT,
            domain="technology",
            language="zh-CN",
            documents=cached_docs,
        )
        result = await pipe.run_request(req, save=False)  # 不自动落盘
        timings["pipeline"] = time.time() - t1
        metrics = result.metrics
        v = metrics.get("verified", 0)
        r = metrics.get("rejected", 0)
        g = metrics.get("generated", 0)
        print(f"  [2] Pipeline:  {timings['pipeline']:.1f}s  (generated={g}, verified={v}, rejected={r})", flush=True)
    except Exception as e:
        timings["pipeline"] = time.time() - t1
        print(f"  [2] Pipeline:  {timings['pipeline']:.1f}s  ERROR: {e}", flush=True)
        return {"dim": dim_name, "keyword": keyword, "verified": 0, "rejected": 0,
                "samples": [], "timings": timings, "error": str(e)}

    # 收集 verified 样本
    samples = []
    if hasattr(result, "verified_samples"):
        samples = result.verified_samples
    elif hasattr(result, "samples"):
        samples = result.samples

    timings["total"] = timings["doc_load"] + timings["pipeline"]
    print(f"  [总] {keyword}: {timings['total']:.1f}s", flush=True)

    return {"dim": dim_name, "keyword": keyword,
            "verified": v, "rejected": r,
            "samples": samples, "timings": timings}


async def main():
    print(f"===== Smoke 6dim×1kw start {time.strftime('%F %T')} =====")
    from benchmark.core.config import settings as _cfg
    print(f"API: {_cfg.llm_api_base}")
    print(f"Model: {_cfg.llm_model}")
    print(f"Output: {OUT_FILE}\n", flush=True)

    grand_start = time.time()
    all_results = []
    all_samples = []

    for idx, (dim, kw) in enumerate(SMOKE_KEYWORDS.items(), 1):
        res = await run_one(dim, kw, idx)
        all_results.append(res)
        all_samples.extend(res.get("samples", []))

    grand_elapsed = time.time() - grand_start

    # 落盘
    with open(OUT_FILE, "w") as f:
        for s in all_samples:
            if hasattr(s, "model_dump"):
                f.write(json.dumps(s.model_dump(), ensure_ascii=False, default=str) + "\n")
            else:
                f.write(json.dumps(s, ensure_ascii=False, default=str) + "\n")

    # ── 汇总报告 ──
    print(f"\n{'='*60}")
    print(f"===== Smoke 6dim×1kw 汇总报告 =====")
    print(f"{'='*60}")

    print(f"\n各维度耗时:")
    print(f"  {'维度':20s} {'keyword':24s} {'文档':>6s} {'Pipeline':>10s} {'总计':>8s} {'verified':>8s}")
    print(f"  {'-'*20} {'-'*24} {'-'*6} {'-'*10} {'-'*8} {'-'*8}")
    for r in all_results:
        t = r["timings"]
        print(f"  {r['dim']:20s} {r['keyword']:24s} {t.get('doc_load',0):5.1f}s {t.get('pipeline',0):9.1f}s {t.get('total',0):7.1f}s {r['verified']:>8d}")

    total_v = sum(r["verified"] for r in all_results)
    total_r = sum(r["rejected"] for r in all_results)
    print(f"\n总计: verified={total_v}, rejected={total_r}, 样本={len(all_samples)}")
    print(f"总耗时: {grand_elapsed:.1f}s ({grand_elapsed/60:.1f} min)")

    # 维度/档位分布
    if all_samples:
        rows = []
        for s in all_samples:
            d = s.model_dump() if hasattr(s, "model_dump") else s
            rows.append(d)
        dims = Counter(r.get("metadata", {}).get("evaluation_dimension", "?") for r in rows)
        levels = Counter(r.get("metadata", {}).get("value_level", "?") for r in rows)
        print(f"\n按维度:")
        for d in sorted(dims):
            print(f"  {d}: {dims[d]}")
        print(f"\n按档位:")
        for lv in ["high", "medium", "low"]:
            print(f"  {lv}: {levels.get(lv, 0)}")

    print(f"\n输出文件: {OUT_FILE}")
    print(f"===== done =====")


if __name__ == "__main__":
    asyncio.run(main())
