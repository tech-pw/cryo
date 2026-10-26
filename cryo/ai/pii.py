"""Embedding-based PII / sensitive-column classification.

Compares column name + type + optional sample values against a library of known-PII
embeddings (Databricks Unity Catalog Data Classification pattern). Pure-Python embeddings
via ``cryo.ai.embeddings`` — no live Iceberg SQL.
"""

from __future__ import annotations

import re
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
    (label, embed_column(label, col_type, sample)) for label, col_type, sample in _PII_LIBRARY
)

DEFAULT_PII_THRESHOLD = 0.72

# High-confidence name hints, checked before embedding similarity. Each hint is a run of
# whole tokens: a column name is split on "_", "-", spaces, digits and camelCase, and the
# hint must appear as consecutive tokens. Substring matching flagged "automobile_type"
# (mobile) and "phone_brand" as phone numbers and blocked compaction on them (issue #168).
_NAME_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("email",), "email"),
    (("e", "mail"), "email"),
    (("emailaddress",), "email"),
    (("phone",), "phone"),
    (("mobile",), "phone"),
    (("msisdn",), "phone"),
    (("telephone",), "phone"),
    (("phonenumber",), "phone"),
    (("ssn",), "ssn"),
    (("social", "security"), "ssn"),
    (("password",), "password"),
    (("passwd",), "password"),
    (("credit", "card"), "credit"),
    (("pan", "number"), "credit"),
    (("birthdate",), "dob"),
    (("dob",), "dob"),
    (("date", "of", "birth"), "dob"),
    (("birth", "date"), "dob"),
    (("salary",), "salary"),
    (("passport",), "passport"),
    (("first", "name"), "first"),
    (("last", "name"), "first"),
    (("full", "name"), "first"),
    (("given", "name"), "first"),
    (("family", "name"), "first"),
    (("middle", "name"), "first"),
    (("firstname",), "first"),
    (("lastname",), "first"),
    (("fullname",), "first"),
    (("surname",), "first"),
    (("username",), "first"),
    (("user", "name"), "first"),
    (("home", "address"), "street"),
    (("street", "address"), "street"),
    (("postal", "address"), "street"),
    (("mailing", "address"), "street"),
    (("ip", "address"), "ip"),
    (("ipv4",), "ip"),
    (("ipv6",), "ip"),
)
# A token that says the column describes a device, app or count rather than holding the
# personal value itself: phone_brand, mobile_os_version, email_count, email_verified.
_QUALIFIER_TOKENS = frozenset(
    {
        "brand",
        "model",
        "os",
        "version",
        "type",
        "count",
        "cnt",
        "num",
        "build",
        "app",
        "make",
        "manufacturer",
        "vendor",
        "carrier",
        "verified",
        "valid",
        "is",
        "has",
        "flag",
        "enabled",
        "opt",
        "domain",
        "length",
        "len",
        "hash",
        "hashed",
        "sha",
        "md5",
        "status",
        "provider",
        "category",
        "class",
        "kind",
    }
)
# "name" alone is everywhere in catalog metadata (table_name, schema_name, brand_name). The
# embedding fallback must not read those as a person's name.
_NON_PERSON_NAME_TOKENS = frozenset(
    {
        "table",
        "schema",
        "column",
        "field",
        "file",
        "host",
        "hostname",
        "brand",
        "product",
        "category",
        "event",
        "app",
        "os",
        "device",
        "model",
        "company",
        "org",
        "organization",
        "team",
        "job",
        "task",
        "bucket",
        "database",
        "db",
        "catalog",
        "namespace",
        "key",
        "metric",
        "service",
        "cluster",
        "pod",
        "node",
        "region",
        "city",
        "country",
        "state",
        "store",
        "merchant",
        "campaign",
        "channel",
        "plan",
        "sku",
        "item",
        "game",
        "course",
        "batch",
        "class",
        "subject",
        "topic",
        "tag",
        "label",
        "browser",
        "page",
        "screen",
        "feature",
        "experiment",
        "variant",
        "source",
        "dataset",
        "view",
        "index",
        "partition",
        "branch",
        "school",
        "center",
        "centre",
        "exam",
        "test",
        "module",
        "chapter",
        "video",
        "image",
        "display",
    }
)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_TOKEN_SEPARATORS = re.compile(r"[^a-z]+")


def name_tokens(name: str) -> tuple[str, ...]:
    """Lower-case word tokens of a column name: split on non-letters and camelCase."""
    spaced = _CAMEL_BOUNDARY.sub(" ", name)
    return tuple(token for token in _TOKEN_SEPARATORS.split(spaced.lower()) if token)


def _contains_run(tokens: tuple[str, ...], run: tuple[str, ...]) -> bool:
    width = len(run)
    return any(tokens[i : i + width] == run for i in range(len(tokens) - width + 1))


def _name_hint_match(name: str) -> tuple[str, float] | None:
    tokens = name_tokens(name)
    if not tokens or _QUALIFIER_TOKENS.intersection(tokens):
        return None
    for run, category in _NAME_HINTS:
        if _contains_run(tokens, run):
            return category, 0.95
    return None


def _embedding_match_allowed(name: str, label: str) -> bool:
    """Whether a column may be classified by embedding similarity to ``label``.

    Character n-grams make "table_name" look like "first name" and "automobile_type" look
    like "mobile". Require a whole shared token, no qualifier token, and for the person-name
    archetype no catalog or product noun next to "name".
    """
    tokens = set(name_tokens(name))
    if not tokens or tokens & _QUALIFIER_TOKENS:
        return False
    label_tokens = set(label.split())
    if not tokens & label_tokens:
        return False
    return not (label.startswith("first name") and tokens & _NON_PERSON_NAME_TOKENS)


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
        if score > best_score and _embedding_match_allowed(name, label):
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
