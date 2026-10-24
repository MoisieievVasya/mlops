"""The 42 → 17 label map, loaded from conf/taxonomy.yaml.

An unknown raw label is mapped to UNMAPPED rather than guessed, dropped or coerced.
The quality gate (check Q4) then fails the batch on any UNMAPPED row, which is how a
silent upstream taxonomy change becomes a loud failure instead of a quietly
mis-labelled month (design R1, 6.8).
"""

from __future__ import annotations

import functools

import yaml

from newsflow.config import TAXONOMY_PATH

#: The value written into dwh.articles.label_category when the map has no entry.
UNMAPPED = "UNMAPPED"


@functools.lru_cache(maxsize=1)
def _load() -> dict:
    with TAXONOMY_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def serving_categories() -> list[str]:
    """The 17, in the order the file declares — also the order of author_desk_prior."""
    return list(_load()["serving_categories"])


def label_map() -> dict[str, str]:
    return dict(_load()["map"])


def taxonomy_version() -> str:
    return str(_load()["version"])


def to_serving(raw_category: str | None) -> str:
    """Map one raw label. Unknown in, UNMAPPED out — never a guess."""
    if not raw_category:
        return UNMAPPED
    return label_map().get(raw_category.strip().upper(), UNMAPPED)


def category_index() -> dict[str, int]:
    """Position of each serving category in the author_desk_prior vector."""
    return {name: i for i, name in enumerate(serving_categories())}
