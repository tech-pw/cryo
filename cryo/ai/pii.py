"""Embedding-based PII / sensitive-column classification.

Compares column name + type + optional sample values against a library of known-PII
embeddings (Databricks Unity Catalog Data Classification pattern). Pure-Python embeddings
via ``cryo.ai.embeddings`` — no live Iceberg SQL.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from cryo.ai.embeddings import cosine_similarity, embed_column

# Known sensitive column archetypes (name fragments + types).
_PII_LIBRARY: tuple[tuple[str, str, str], ...] = (
    ("email address mail", "string", "user@example.com"),
    ("phone mobile telephone", "string", "+15551234567"),
    ("ssn social security tax id", "string", "123-45-6789"),
    ("credit card pan payment", "string", "4111111111111111"),
    ("password secret token credential", "string", "********"),
    ("first name last name full name", "string", "John Doe"),
    ("date of birth dob birthdate", "date", "1990-01-01"),
    ("street address zip postal city", "string", "123 Main St"),
    ("ip address ipv4 ipv6", "string", "192.168.1.1"),
    ("national id passport license", "string", "AB1234567"),
    ("salary income compensation wage", "decimal", "75000.00"),
    ("medical diagnosis health patient", "string", "ICD-10"),
)

_PII_EMBEDDINGS = tuple(
    (label, embed_column(label, col_type, sample))
    for label, col_type, sample in _PII_LIBRARY
)

DEFAULT_PII_THRESHOLD = 0.72

# High-confidence name fragments — checked before embedding similarity.
_NAME_HINTS: tuple[tuple[str, str], ...] = (
    ("email", "email"),
    ("e_mail", "email"),
    ("phone", "phone"),
    ("mobile", "phone"),
    ("ssn", "ssn"),
    ("social_security", "ssn"),
    ("password", "password"),
    ("passwd", "password"),
    ("credit_card", "credit"),
    ("pan_number", "credit"),
    ("birthdate", "dob"),
    ("date_of_birth", "dob"),
    ("salary", "salary"),
    ("passport", "passport"),
)


def _name_hint_match(name: str) -> tuple[str, float] | None:
    lowered = name.lower().replace("-", "_")
    for fragment, category in _NAME_HINTS:
        if fragment in lowered:
            return category, 0.95
    return None


@dataclass(frozen=True)
class PiiMatch:
    column_name: str
    column_type: str
    pii_category: str
    similarity: float
    already_tagged: bool


def classify_column(
    name: str,
    col_type: str = "",
    *,
    sample_value: str = "",
    existing_tags: Sequence[str] | None = None,
    threshold: float = DEFAULT_PII_THRESHOLD,
) -> PiiMatch | None:
    """Return a PII match when similarity exceeds threshold."""
    tags = {t.lower() for t in (existing_tags or ())}
    tagged = any(
        kw in name.lower() or any(kw in t for t in tags)
        for kw in ("pii", "sensitive", "confidential", "restricted")
    )
    vector = embed_column(name, col_type, sample_value)
    best_label = ""
    best_score = 0.0
    hint = _name_hint_match(name)
    if hint is not None:
        best_label, best_score = hint[0], hint[1]
    for label, ref in _PII_EMBEDDINGS:
        score = cosine_similarity(vector, ref)
        if score > best_score:
            best_score = score
            best_label = label
    if best_score < threshold:
        return None
    return PiiMatch(
        column_name=name,
        column_type=col_type,
        pii_category=best_label.split()[0],
        similarity=best_score,
        already_tagged=tagged,
    )


def classify_table_columns(
    columns: Sequence[dict[str, Any]],
    *,
    threshold: float = DEFAULT_PII_THRESHOLD,
) -> list[PiiMatch]:
    """Classify columns from scanner ``column_quality`` or catalog metadata dicts."""
    matches: list[PiiMatch] = []
    for col in columns:
        name = str(col.get("column_name") or col.get("name") or "")
        if not name:
            continue
        col_type = str(col.get("column_type") or col.get("type") or "")
        sample = str(col.get("sample_value") or col.get("sample") or "")
        tags = col.get("tags") or col.get("classification_tags") or []
        if not isinstance(tags, list):
            tags = []
        match = classify_column(
            name,
            col_type,
            sample_value=sample,
            existing_tags=[str(t) for t in tags],
            threshold=threshold,
        )
        if match is not None and not match.already_tagged:
            matches.append(match)
    return matches
