"""Lightweight text embeddings for fleet metadata intelligence.

Uses character n-gram feature hashing — no external ML dependencies, deterministic,
and sufficient for column-name / description similarity (Unity Catalog / OpenMetadata
pattern). Vectors are L2-normalized for cosine similarity via dot product.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

_TOKEN_SPLIT = re.compile(r"[^a-z0-9_]+")
_DEFAULT_DIM = 128


def normalize_text(text: str) -> str:
    return _TOKEN_SPLIT.sub(" ", text.lower()).strip()


def embed_text(text: str, *, dim: int = _DEFAULT_DIM) -> tuple[float, ...]:
    """Hash n-gram features into a fixed-size unit vector."""
    normalized = normalize_text(text)
    if not normalized:
        return tuple(0.0 for _ in range(dim))

    vec = [0.0] * dim
    padded = f" {normalized} "
    for n in (2, 3, 4):
        for i in range(len(padded) - n + 1):
            gram = padded[i : i + n]
            idx = hash(gram) % dim
            vec[idx] += 1.0

    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 0:
        return tuple(0.0 for _ in range(dim))
    return tuple(v / norm for v in vec)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    return round(sum(a * b for a, b in zip(left, right, strict=True)), 4)


def embed_column(name: str, col_type: str = "", sample: str = "") -> tuple[float, ...]:
    return embed_text(f"{name} {col_type} {sample}".strip())


def embed_table(
    schema_name: str,
    table_name: str,
    *,
    description: str = "",
    stats_summary: str = "",
) -> tuple[float, ...]:
    return embed_text(f"{schema_name}.{table_name} {description} {stats_summary}".strip())
