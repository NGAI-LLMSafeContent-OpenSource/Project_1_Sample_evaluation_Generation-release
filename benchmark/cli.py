from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich import print

from benchmark.agents.skills import SkillRegistry
from benchmark.pipelines.evaluation import EvaluationPipeline
from benchmark.pipelines.generation import GenerationPipeline
from benchmark.pipelines.sample_generation import SampleGenerationPipeline
from benchmark.postprocessors.experience_to_qa import ExperienceToQAPostprocessor
from benchmark.schemas import ErrorSample, SampleType, SkillGenerationRequest, TaskType
from benchmark.storage.db import init_db as _init_storage
from benchmark.storage.repository import BenchmarkRepository

app = typer.Typer(help="EvoBench CLI")


def _coerce_cli_value(raw: str):
    text = raw.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _parse_skill_config(raw_items: list[str] | None) -> dict:
    if not raw_items:
        return {}
    parsed: dict = {}
    for item in raw_items:
        if "=" not in item:
            raise typer.BadParameter("--skill-config must use key=value form.")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise typer.BadParameter("--skill-config key cannot be empty.")
        parsed[key] = _coerce_cli_value(value)
    return parsed


def _paper_template_path() -> Path:
    return Path(__file__).resolve().parent / "agents" / "skills" / "templates" / "paper_corpus_template.jsonl"


@app.command("init-db")
@app.command("init-storage")
def init_storage() -> None:
    _init_storage()
    print("[green]JSONL storage initialized.[/green]")


@app.command("list-skills")
def list_skills(task_type: str | None = None) -> None:
    parsed = TaskType(task_type) if task_type else None
    for skill in SkillRegistry().list_definitions(parsed):
        print(f"[cyan]{skill.skill_id}[/cyan] | {skill.task_type.value} | {skill.description}")


@app.command("generate-samples")
def generate_samples(
    topic: str | None = typer.Option(None, help="Topic keyword for retriever. Mutually exclusive with --dimension."),
    dimension: str | None = typer.Option(None, "--dimension", "-d", help="一级或二级维度名，从 yaml 随机选主题。与 --topic 互斥。"),
    n_topics: int = typer.Option(1, "--n-topics", "-n", min=1, help="从维度中随机抽取的主题数（仅与 --dimension 搭配有效）。"),
    groups_per_topic: int = typer.Option(1, "--groups-per-topic", "-g", min=1, help="每个主题生成的三联组数（1组=3条: high/medium/low）。"),
    seed: int | None = typer.Option(None, "--seed", help="随机种子，固定后可复现相同的主题抽取结果（仅与 --dimension 搭配有效）。"),
    corpus_jsonl: Path | None = typer.Option(None, help="Read source documents from a corpus.jsonl file."),
    samples_jsonl: Path | None = typer.Option(None, help="Write generated UnifiedSample records to this JSONL path."),
    task_type: str | None = typer.Option(None, help="document_to_x/document_to_xy/document_to_xty/error_to_training_set"),
    skill_ids: str = typer.Option("doc_to_qa", help="Comma-separated skill ids."),
    skill_config: list[str] | None = typer.Option(
        None,
        help="Repeatable skill config in key=value form, e.g. --skill-config pairs_per_doc=3.",
    ),
    limit: int = typer.Option(10, min=1, help="Max number of individual samples (fallback when neither topic nor dimension specified)."),
    domain: str = "technology",
    language: str = "zh-CN",
) -> None:
    # --- 参数互斥校验 ---
    if topic and dimension:
        raise typer.BadParameter("--topic 和 --dimension 不能同时指定，请选其一。")

    _skill_list = [s.strip() for s in skill_ids.split(",") if s.strip()]

    # --- 确定主题列表 ---
    if topic:
        # 单主题模式
        topics = [topic]
    elif dimension:
        # 维度模式：从 yaml 随机抽取
        from benchmark.keywords import pick_random_topics
        topics = pick_random_topics(dimension=dimension, n_topics=n_topics, seed=seed)
        _seed_note = f"（seed={seed}，可复现）" if seed is not None else ""
        print(f"[cyan]从维度 '{dimension}' 随机选取 {len(topics)} 个主题{_seed_note}: {topics}[/cyan]")
    else:
        # 无维度无主题
        if "value_qa" in _skill_list:
            raise typer.BadParameter(
                "value_qa 技能需要检索主题驱动生成，请指定 --topic 或 --dimension。\n"
                "示例：\n"
                "  --topic \"中秋节\" --groups-per-topic 2\n"
                "  --dimension \"文化元素符合度\" --n-topics 5"
            )
        # 其他 skill 使用 limit 兜底（向后兼容）
        topics = [None]  # type: ignore[list-item]

    # --- 逐主题生成 ---
    total_generated = 0
    total_verified = 0
    total_rejected = 0
    effective_limit = groups_per_topic * 3 if (topic or dimension) else limit

    for idx, t in enumerate(topics, 1):
        if len(topics) > 1:
            print(f"\n[bold][ {idx}/{len(topics)} ] topic = {t}[/bold]")
        request = SkillGenerationRequest(
            topic=t,
            corpus_jsonl_path=str(corpus_jsonl) if corpus_jsonl else None,
            samples_jsonl_path=str(samples_jsonl) if samples_jsonl else None,
            task_type=TaskType(task_type) if task_type else None,
            skill_ids=[skill_id.strip() for skill_id in skill_ids.split(",") if skill_id.strip()],
            skill_config=_parse_skill_config(skill_config),
            limit=effective_limit,
            domain=domain,
            language=language,
        )
        result = asyncio.run(SampleGenerationPipeline().run_request(request))
        total_generated += result.metrics['generated']
        total_verified += result.metrics['verified']
        total_rejected += result.metrics['rejected']
        _groups = result.metrics['verified'] // 3
        print(
            f"[green]  Generated {result.metrics['generated']} samples ({_groups} groups); "
            f"verified {result.metrics['verified']}; rejected {result.metrics['rejected']}.[/green]"
        )
        # 产出不足警告
        if (topic or dimension) and _groups < groups_per_topic:
            shortfall = groups_per_topic - _groups
            print(
                f"[yellow]  ⚠️ 请求 {groups_per_topic} 组但仅生成 {_groups} 组（差 {shortfall} 组）。"
                f"原因：主题 '{t}' 的检索文档或可提取事实不足，无法产出更多三联组。[/yellow]"
            )
        for sample in result.samples[:3]:
            print(f"  - {sample.sample_id} | {sample.skill_id} | x={str(sample.x)[:60]}")

    # --- 汇总 ---
    if len(topics) > 1:
        _total_groups = total_verified // 3
        print(
            f"\n[bold green]===== 汇总: {len(topics)} 个主题, "
            f"{total_generated} generated, {total_verified} verified ({_total_groups} groups), "
            f"{total_rejected} rejected =====[/bold green]"
        )


@app.command("generate-from-errors")
def generate_from_errors(
    errors_json: Path = typer.Option(..., help="JSON file containing a list of ErrorSample records."),
    limit: int = typer.Option(20, min=1),
) -> None:
    raw = json.loads(errors_json.read_text(encoding="utf-8"))
    errors = [ErrorSample.model_validate(item) for item in raw]
    request = SkillGenerationRequest(
        task_type=TaskType.error_to_training_set,
        skill_ids=["error_to_training_samples"],
        limit=limit,
        error_samples=errors,
    )
    result = asyncio.run(SampleGenerationPipeline().run_request(request))
    print(f"[green]Generated {result.metrics['verified']} training samples from {len(errors)} errors.[/green]")


@app.command("list-samples")
def list_samples(
    status: str | None = None,
    task_type: str | None = None,
    skill_id: str | None = None,
    limit: int = 20,
) -> None:
    parsed = TaskType(task_type) if task_type else None
    samples = BenchmarkRepository().list_samples(status=status, task_type=parsed, skill_id=skill_id, limit=limit)
    for sample in samples:
        print(f"[cyan]{sample.sample_id}[/cyan] | {sample.status} | {sample.task_type.value} | {sample.skill_id} | x={str(sample.x)[:80]}")


@app.command("list-corpus")
def list_corpus(limit: int = 20, topic: str | None = None, source_type: str | None = None) -> None:
    docs = BenchmarkRepository().list_documents(limit=limit, topic=topic, source_type=source_type)
    for doc in docs:
        print(f"[cyan]{doc.source_id}[/cyan] | {doc.source_type} | {doc.title[:80]}")


@app.command("init-paper-corpus-template")
def init_paper_corpus_template(
    output: Path = typer.Option(Path("data/paper_corpus_template.jsonl"), help="Destination for the paper corpus template."),
    overwrite: bool = typer.Option(False, help="Overwrite the destination if it already exists."),
) -> None:
    template_path = _paper_template_path()
    if output.exists() and not overwrite:
        raise typer.BadParameter(f"{output} already exists. Use --overwrite to replace it.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(template_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[green]Wrote paper corpus template to {output}[/green]")


@app.command("postprocess-experience-to-qa")
def postprocess_experience_to_qa(
    source_skill_id: str = typer.Option("paper_to_experience", help="Source skill id to transform."),
    status: str | None = typer.Option("verified", help="Only transform samples with this status. Use empty value to include all."),
    samples_jsonl: Path | None = typer.Option(None, help="Read and write UnifiedSample records from this JSONL path."),
    limit: int = typer.Option(100, min=1),
) -> None:
    repo = BenchmarkRepository(samples_path=samples_jsonl) if samples_jsonl else BenchmarkRepository()
    source_samples = repo.list_samples(status=status or None, skill_id=source_skill_id, limit=limit)
    qa_samples = ExperienceToQAPostprocessor().transform(source_samples, limit=limit, source_skill_id=source_skill_id)
    for sample in qa_samples:
        repo.upsert_sample(sample)
    print(f"[green]Generated {len(qa_samples)} experience_to_qa samples from {len(source_samples)} source samples.[/green]")


@app.command("ingest-sample")
def ingest_sample(
    topic: str = "AI",
    limit: int = 5,
    sample_types: str = typer.Option(
        "fact_verification,source_attribution,evidence_selection,temporal_awareness,abstention,data_value_judgement,conflict_resolution,causal_attribution",
        help="Comma-separated sample types.",
    ),
) -> None:
    parsed = [SampleType(sample_type.strip()) for sample_type in sample_types.split(",") if sample_type.strip()]
    count = asyncio.run(GenerationPipeline().run(topic=topic, limit=limit, sample_types=parsed))
    print(f"[green]Generated and saved {count} verified benchmark items.[/green]")


@app.command("run-eval")
def run_eval(model_id: str = "demo_model") -> None:
    result = asyncio.run(EvaluationPipeline().evaluate_mock_model(model_id))
    print(result)


if __name__ == "__main__":
    app()
