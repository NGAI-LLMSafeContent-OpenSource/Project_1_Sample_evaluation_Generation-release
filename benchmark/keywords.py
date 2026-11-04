"""Keyword pool loader & random topic picker for value_qa skill.

Reads configs/value_qa_keywords.yaml and provides:
- Dimension hierarchy resolution (primary → secondary → scene → topics)
- Random topic sampling filtered by dimension
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import yaml

_KEYWORDS_PATH = Path(__file__).resolve().parent.parent / "configs" / "value_qa_keywords.yaml"

_cache: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    """Lazy-load and cache the keywords yaml."""
    global _cache
    if _cache is None:
        _cache = yaml.safe_load(_KEYWORDS_PATH.read_text(encoding="utf-8")) or {}
    return _cache


def get_primary_dimensions() -> dict[str, list[str]]:
    """Return {一级维度: [二级维度名, ...]}."""
    return _load().get("primary_dimensions", {})


def get_all_secondary_dimensions() -> list[str]:
    """Return list of all secondary dimension names."""
    return list(_load().get("keywords", {}).keys())


def get_topics_for_dimension(dimension: str | None) -> list[str]:
    """Get all topic keywords matching the given dimension name.

    Args:
        dimension: A primary or secondary dimension name.
            - If it matches a primary dimension → collect all topics from its secondary dims.
            - If it matches a secondary dimension → collect topics from that dim only.
            - If None → collect all topics across all dimensions.

    Returns:
        Flat list of topic strings.
    """
    data = _load()
    keywords = data.get("keywords", {})
    primary_dims = data.get("primary_dimensions", {})

    if dimension is None:
        # All topics
        target_dims = list(keywords.keys())
    elif dimension in primary_dims:
        # Primary dimension → its secondary dims
        target_dims = primary_dims[dimension]
    elif dimension in keywords:
        # Exact secondary dimension
        target_dims = [dimension]
    else:
        raise ValueError(
            f"未知维度 '{dimension}'。"
            f"可用一级维度: {list(primary_dims.keys())}; "
            f"可用二级维度: {list(keywords.keys())}"
        )

    topics: list[str] = []
    for dim_name in target_dims:
        scenes = keywords.get(dim_name, {})
        for _scene_name, topic_list in scenes.items():
            topics.extend(topic_list)
    return topics


def pick_random_topics(
    dimension: str | None = None,
    n_topics: int = 1,
    *,
    seed: int | None = None,
) -> list[str]:
    """Randomly sample n_topics from the keyword pool (filtered by dimension).

    Args:
        dimension: Primary or secondary dimension name, or None for all.
        n_topics: Number of topics to pick (without replacement).
        seed: Optional random seed for reproducibility.

    Returns:
        List of sampled topic strings.

    Raises:
        ValueError: If dimension is unknown or n_topics exceeds pool size.
    """
    pool = get_topics_for_dimension(dimension)
    if n_topics > len(pool):
        raise ValueError(
            f"请求 {n_topics} 个主题，但维度 '{dimension or '全部'}' 仅有 {len(pool)} 个可用主题。"
        )
    rng = random.Random(seed)
    return rng.sample(pool, n_topics)
