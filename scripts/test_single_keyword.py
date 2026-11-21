"""单 keyword 验证脚本：跑丝绸之路，检查 medium 生成效果。"""
import asyncio
import json
from pathlib import Path
from benchmark.pipelines.sample_generation import SampleGenerationPipeline
from benchmark.schemas import SkillGenerationRequest, SourceDocument

CORPUS_DIR = Path("data/corpus/baidu")


def load_cached_docs(topic: str) -> list:
    """从本地 corpus 缓存加载文档。"""
    exact_path = CORPUS_DIR / f"{topic}.jsonl"
    if exact_path.exists():
        docs = []
        for line in exact_path.read_text().splitlines():
            if line.strip():
                docs.append(SourceDocument.model_validate(json.loads(line)))
        if docs:
            return docs
    return []


async def run():
    topic = "丝绸之路"
    cached_docs = load_cached_docs(topic)
    print(f"loaded {len(cached_docs)} cached docs for '{topic}'")

    pipe = SampleGenerationPipeline()
    req = SkillGenerationRequest(
        topic=topic,
        skill_ids=["value_qa"],
        limit=9,
        domain="technology",
        language="zh-CN",
        documents=cached_docs,
    )
    print("[1] selecting docs...", flush=True)
    selected_docs = pipe._select_docs(cached_docs, req)
    print(f"[2] selected {len(selected_docs)} docs, generating samples...", flush=True)
    samples = await pipe.factory.generate(req, documents=selected_docs, error_samples=None)
    print(f"[3] generated {len(samples)} samples, verifying...", flush=True)
    verified = pipe.verifier.verify_samples(samples)
    accepted = [s for s in verified if s.status == "verified"]
    print(f"[4] verified: {len(accepted)} accepted, {len(verified)-len(accepted)} rejected", flush=True)
    print(f"metrics: generated={len(samples)}, verified={len(accepted)}")

    # 找出 medium 样本
    for s in accepted:
        d = s.model_dump() if hasattr(s, 'model_dump') else s
        meta = d.get("metadata", {})
        if meta.get("value_level") == "medium":
            ans = ""
            for art in d.get("output", {}).get("artifacts", []):
                if art.get("role") == "answer":
                    ans = art.get("value", "")
            annots = d.get("value_annotations", [])
            neutral = [a for a in annots if a.get("polarity") == "neutral"]
            print(f"\n--- medium [{meta.get('evaluation_dimension')}] pattern={meta.get('medium_pattern')} ---")
            print(f"answer: {ans[:400]}")
            print(f"neutral_annotations: {json.dumps(neutral, ensure_ascii=False, indent=2)[:600]}")
            print()


if __name__ == "__main__":
    asyncio.run(run())
