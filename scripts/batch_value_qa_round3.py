#!/usr/bin/env python3
"""Round3: prompt-v3 验证批次，6维度×5新关键词，gpt-5.5 生成。
直接调用 pipeline，本地 corpus 缓存优先，无缓存走百度在线检索。

用法: PYTHONPATH=. python -u scripts/batch_value_qa_round3.py 2>&1 | tee logs/value_qa_round3.log
"""

import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

# 确保 bootstrap 激活
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import benchmark.bootstrap  # noqa: E402, F401

from benchmark.pipelines.sample_generation import SampleGenerationPipeline
from benchmark.schemas import SkillGenerationRequest


# ═══════════════ 6 维度 × 5 NEW keyword（prompt-v3 验证，全新主题）═══════════════
DIMENSIONS = {
    "文化元素符合度": [
        "龙舟", "年画", "扎染", "风筝", "古琴",
    ],
    "行为社会规范符合度": [
        "谦让", "家训", "拜师礼", "座次礼仪", "丧事从简",
    ],
    "价值观忠实度": [
        "抗美援朝精神", "焦裕禄精神", "载人航天精神", "红旗渠精神", "西柏坡精神",
    ],
    "公平合规度": [
        "高考加分政策", "农民工权益", "最低工资标准", "保障性住房", "司法救助",
    ],
    "伤害风险合规度": [
        "疫苗管理法", "危险化学品安全", "溺水预防", "家用电器安全", "特种设备安全",
    ],
    "隐私合规度": [
        "健康码数据", "学生信息保护", "快递实名制", "智能家居监听", "基因信息保护",
    ],
}

CONCURRENCY = 3  # 并发路数（提升到3加速）

LIMIT = 12
MAX_RETRY = 2  # 减少重试次数加速
CORPUS_DIR = Path("data/corpus/baidu")


def load_cached_docs(topic: str) -> list:
    """尝试从本地 corpus 缓存加载文档，避免网络依赖。"""
    from benchmark.schemas import SourceDocument

    # 精确匹配
    exact_path = CORPUS_DIR / f"{topic}.jsonl"
    if exact_path.exists():
        docs = []
        for line in exact_path.read_text().splitlines():
            if line.strip():
                docs.append(SourceDocument.model_validate(json.loads(line)))
        if docs:
            return docs

    # 模糊匹配：topic 是文件名的子串
    for p in CORPUS_DIR.glob("*.jsonl"):
        if topic in p.stem or p.stem in topic:
            docs = []
            for line in p.read_text().splitlines():
                if line.strip():
                    docs.append(SourceDocument.model_validate(json.loads(line)))
            if docs:
                return docs
    return []


async def generate_one(pipe: SampleGenerationPipeline, topic: str) -> dict:
    """单个 keyword 生成，返回 metrics dict。"""
    # 优先从本地缓存加载文档
    cached_docs = load_cached_docs(topic)

    req = SkillGenerationRequest(
        topic=topic,
        skill_ids=["value_qa"],
        limit=LIMIT,
        domain="technology",
        language="zh-CN",
        documents=cached_docs,  # 空 list 时 pipeline 会 fallback 到 retriever
    )
    result = await pipe.run_request(req, save=True)
    return result.metrics


async def run_one_keyword(topic: str, idx: int, total: int, dim_name: str) -> dict:
    """单个 keyword 的完整生成流程（含重试），返回 {verified, rejected}。"""
    print(f"\n[{idx}/{total}] [{dim_name}] keyword={topic}", flush=True)
    for try_num in range(1, MAX_RETRY + 1):
        try:
            pipe = SampleGenerationPipeline()
            metrics = await generate_one(pipe, topic)
            v = metrics["verified"]
            r = metrics["rejected"]
            print(f"  [try {try_num}] Generated {metrics['generated']}; verified {v}; rejected {r}", flush=True)
            if v > 0:
                return {"verified": v, "rejected": r}
        except Exception as e:
            print(f"  [try {try_num}] ERROR: {e}", flush=True)
        if try_num < MAX_RETRY:
            await asyncio.sleep(3)
    print(f"  ⚠️ {topic} 重试均失败", flush=True)
    return {"verified": 0, "rejected": 0}


async def main():
    # 清空目标文件
    verified_path = Path("data/samples/value_qa/verified.jsonl")
    rejected_path = Path("data/samples/value_qa/rejected.jsonl")
    verified_path.write_text("")
    rejected_path.write_text("")

    # 构建任务列表
    tasks_list = []  # (keyword, dim_name)
    for dim_name, keywords in DIMENSIONS.items():
        for kw in keywords:
            tasks_list.append((kw, dim_name))

    total_kw = len(tasks_list)
    total_verified = 0
    total_rejected = 0
    start_time = time.time()

    print(f"===== Round3 batch start {time.strftime('%F %T')} total_calls={total_kw} concurrency={CONCURRENCY} =====")
    print(f"verified → {verified_path}")
    print(flush=True)

    # 并发执行，每次 CONCURRENCY 个
    for batch_start in range(0, total_kw, CONCURRENCY):
        batch = tasks_list[batch_start:batch_start + CONCURRENCY]
        coros = [
            run_one_keyword(kw, batch_start + i + 1, total_kw, dim)
            for i, (kw, dim) in enumerate(batch)
        ]
        results = await asyncio.gather(*coros)
        for r in results:
            total_verified += r["verified"]
            total_rejected += r["rejected"]
        await asyncio.sleep(1)  # batch 间隔

    elapsed = time.time() - start_time
    print(f"\n===== Round3 batch end {time.strftime('%F %T')} (elapsed {elapsed/60:.1f} min) =====\n")

    # 统计
    rows = [json.loads(l) for l in verified_path.read_text().splitlines() if l.strip()]
    dims = Counter(r.get("metadata", {}).get("evaluation_dimension", "?") for r in rows)
    levels = Counter(r.get("metadata", {}).get("value_level", "?") for r in rows)
    triplets = len(rows) // 3

    print(f"总样本数: {len(rows)} ({triplets} 组三联组)")
    print(f"\n按维度分布:")
    for d in sorted(dims.keys()):
        n = dims[d]
        groups = n // 3
        print(f"  {d:12s}: {n:3d} 条 ({groups:2d} 组)")
    print(f"\n按档位分布:")
    for lv in ["high", "medium", "low"]:
        print(f"  {lv}: {levels.get(lv, 0)}")

    # 重命名为 round3 带模型标识文件
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = verified_path.parent / f"value_qa_gpt55_round3_6dim_{timestamp}.jsonl"
    verified_path.rename(out_path)
    print(f"\n已保存: {out_path}")

    # 恢复原始 verified.jsonl
    backups = sorted(verified_path.parent.glob("verified.pre_round3_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if backups:
        import shutil
        shutil.copy2(backups[0], verified_path)
        print(f"已恢复: {verified_path} (from {backups[0].name})")


if __name__ == "__main__":
    asyncio.run(main())
