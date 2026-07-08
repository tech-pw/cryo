"""EasyTUS-style unionable-table search.

Based on: "EasyTUS: A Framework for Easy Table Union Search" — arXiv:2511.02674.

Finds tables whose schema is compatible enough to be ``UNION ALL``'d with a
target table, ranked by weighted Jaccard similarity over column names and
column-type signatures. Optionally enriches the rationale by asking the
configured LLM for a one-line assessment; when no LLM is configured it
falls back to a deterministic explanation so the endpoint never depends on
mocked completions.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.ai.runtime import LLMInvokeContext, complete_with_budget_and_observation
from cryo.db.models import Source, Table

logger = logging.getLogger(__name__)

NAME_WEIGHT = 0.6
TYPE_WEIGHT = 0.4

# Columns that can be safely auto-cast across these pairs when UNIONing. This
# is intentionally small — anything outside this table falls back to "type
# mismatch" so we never silently produce wrong results.
COERCIBLE_TYPES: dict[tuple[str, str], str] = {
    ("bigint", "decimal"): "CAST(... AS DECIMAL)",
    ("bigint", "double"): "CAST(... AS DOUBLE)",
    ("integer", "bigint"): "CAST(... AS BIGINT)",
    ("integer", "decimal"): "CAST(... AS DECIMAL)",
    ("varchar", "string"): "identity",
    ("string", "varchar"): "identity",
    ("timestamp", "date"): "CAST(... AS TIMESTAMP)",
}


def _normalize_type(col_type: str | None) -> str:
    if not col_type:
        return ""
    return col_type.strip().lower().split("(", 1)[0]


def _extract_columns_from_table_obj(table_obj: Any) -> list[tuple[str, str]]:
    """Return ``[(column_name, normalized_type), ...]`` for an Iceberg table object.

    Tolerates several PyIceberg shapes (Schema.fields, list of NestedField).
    """
    schema = getattr(table_obj, "schema", None)
    if schema is None:
        return []

    fields: Iterable[Any]
    if hasattr(schema, "fields"):
        fields = list(getattr(schema, "fields", []) or [])
    elif isinstance(schema, dict):
        fields = list(schema.get("fields", []))
    else:
        fields = list(schema)

    cols: list[tuple[str, str]] = []
    for field in fields:
        name = getattr(field, "name", None) or getattr(field, "field_name", None)
        ftype = (
            getattr(field, "field_type", None)
            or getattr(field, "type", None)
            or getattr(field, "data_type", None)
        )
        type_str = str(ftype) if ftype is not None else ""
        if not name:
            continue
        cols.append((str(name), _normalize_type(type_str)))
    cols.sort()
    return cols


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    union = sa | sb
    if not union:
        return 0.0
    return len(sa & sb) / len(union)


def _format_qualified(identifier: str) -> str:
    """Return a SQL-safe ``schema.table`` literal (double-quoted)."""
    if "." in identifier:
        schema_part, _, table_part = identifier.partition(".")
        return f'"{schema_part}"."{table_part}"'
    return f'"{identifier}"'


def _select_list(target_cols: list[tuple[str, str]]) -> str:
    return ", ".join(f'"{name}"' for name, _ in target_cols)


def _build_union_sql(
    target_identifier: str,
    target_cols: list[tuple[str, str]],
    other_identifier: str,
    other_cols: list[tuple[str, str]],
    overlap_columns: list[str],
    cast_columns: list[str],
) -> str:
    target_select = _select_list(target_cols)
    other_by_name = {n: t for n, t in other_cols}
    overlap_set = set(overlap_columns)
    cast_set = set(cast_columns)

    other_select_parts: list[str] = []
    for name, ttype in target_cols:
        if name in other_by_name:
            other_ttype = other_by_name[name]
            if ttype == other_ttype:
                other_select_parts.append(f'"{name}"')
            elif name in overlap_set and name in cast_set:
                other_select_parts.append(f'CAST("{name}" AS {ttype})')
            else:
                other_select_parts.append(f'CAST(NULL AS {ttype or "VARCHAR"}) AS "{name}"')
        else:
            other_select_parts.append(f'CAST(NULL AS {ttype or "VARCHAR"}) AS "{name}"')
    other_select = ", ".join(other_select_parts)

    return (
        f"SELECT {target_select} FROM {_format_qualified(target_identifier)}\n"
        f"UNION ALL\n"
        f"SELECT {other_select} FROM {_format_qualified(other_identifier)}"
    )


def _deterministic_rationale(
    target_cols: list[tuple[str, str]],
    overlap_columns: list[str],
    cast_columns: list[str],
) -> str:
    target_count = len(target_cols)
    overlap_count = len(overlap_columns)
    cast_count = len(cast_columns)
    if overlap_count == target_count and cast_count == 0:
        return (
            f"{overlap_count}/{target_count} columns overlap exactly — direct UNION ALL supported."
        )
    if overlap_count + cast_count >= target_count:
        return (
            f"{overlap_count}/{target_count} columns overlap, "
            f"{cast_count} type-compatible with coercion (e.g. bigint ↔ decimal)."
        )
    return (
        f"{overlap_count}/{target_count} columns overlap, "
        f"{cast_count} type-compatible with coercion, "
        f"remainder projected as NULL."
    )


@dataclass(frozen=True)
class UnionScore:
    target_table_id: str
    candidate_table_id: str
    score: float
    overlap_columns: list[str]
    union_sql: str
    rationale: str
    candidate_schema: str
    candidate_table_name: str = ""
    candidate_schema_name: str = ""


@dataclass
class _TableSchema:
    table_id: uuid.UUID
    schema_name: str
    table_name: str
    columns: list[tuple[str, str]]


def _fetch_table_columns(
    session: Session,
    table: Table,
    catalog_cache: dict[uuid.UUID, Any],
) -> list[tuple[str, str]]:
    """Resolve ``table``'s columns via its Source's catalog. Cached per source."""
    cached = catalog_cache.get(table.source_id)
    if cached is None:
        source = session.get(Source, table.source_id)
        if source is None:
            return []
        try:
            from cryo.sources.factory import source_catalog_for

            catalog = source_catalog_for(source)
        except Exception as exc:
            logger.debug("Could not load catalog for source %s: %s", table.source_id, exc)
            catalog_cache[table.source_id] = False
            return []
        catalog_cache[table.source_id] = catalog
        cached = catalog

    if cached is False:
        return []

    try:
        table_obj = cached.load_table(table.schema_name, table.table_name)
        return _extract_columns_from_table_obj(table_obj)
    except Exception as exc:
        logger.debug("Could not load or parse table %s.%s: %s", table.schema_name, table.table_name, exc)
        return []


def _score_candidate(
    target: _TableSchema,
    candidate: _TableSchema,
) -> tuple[float, list[str], list[str]]:
    target_names = [n for n, _ in target.columns]
    candidate_names = [n for n, _ in candidate.columns]
    target_types = [t for _, t in target.columns]
    candidate_types = [t for _, t in candidate.columns]

    name_sim = _jaccard(target_names, candidate_names)
    type_sim = _jaccard(target_types, candidate_types)
    score = round(NAME_WEIGHT * name_sim + TYPE_WEIGHT * type_sim, 4)

    target_name_set = {n for n, _ in target.columns}
    candidate_by_name = {n: t for n, t in candidate.columns}
    overlap = sorted(n for n in target_name_set if n in candidate_by_name)
    cast: list[str] = []
    for name in overlap:
        t_t = next((t for n, t in target.columns if n == name), "")
        o_t = candidate_by_name[name]
        if t_t == o_t:
            continue
        if (o_t, t_t) in COERCIBLE_TYPES:
            cast.append(name)
    return score, overlap, cast


async def _maybe_llm_rationale(
    session: Session | None,
    target: _TableSchema,
    candidate: _TableSchema,
    overlap: list[str],
    cast: list[str],
    deterministic: str,
) -> str:
    if session is None:
        return deterministic
    try:
        prompt = (
            "You are an Iceberg data engineer. Explain in one sentence why these two tables "
            "could be UNION ALL'd together.\n\n"
            f"Target: {target.schema_name}.{target.table_name}\n"
            f"Candidate: {candidate.schema_name}.{candidate.table_name}\n"
            f"Overlapping columns ({len(overlap)}): {', '.join(overlap[:12])}\n"
            f"Type-coercible columns: {', '.join(cast[:8]) or 'none'}\n\n"
            "Return a single concise rationale sentence."
        )
        response, _observation = await asyncio.wait_for(
            complete_with_budget_and_observation(
                session,
                prompt,
                invoke=LLMInvokeContext(
                    feature="unionable_rationale",
                    prompt_name="unionable_rationale",
                    table_id=target.table_id,
                    extra={"candidate_table_id": str(candidate.table_id)},
                ),
                max_tokens=120,
            ),
            timeout=10.0,
        )
        if response is None:
            return deterministic
        text = response.content.strip().splitlines()[0] if response.content else deterministic
        return text or deterministic
    except Exception as exc:
        logger.debug("LLM rationale unavailable, falling back: %s", exc)
        return deterministic


def find_unionable(
    session: Session,
    target_table_id: uuid.UUID,
    k: int = 10,
    *,
    use_llm: bool = True,
) -> list[UnionScore]:
    """Return the top-``k`` unionable tables for ``target_table_id``.

    Reads each table's schema via its source's catalog, computes a weighted
    Jaccard similarity (60% column-name, 40% column-type), and generates a
    candidate ``UNION ALL`` statement.
    """
    target_table = session.get(Table, target_table_id)
    if target_table is None:
        return []

    catalog_cache: dict[uuid.UUID, Any] = {}
    target_columns = _fetch_table_columns(session, target_table, catalog_cache)
    if not target_columns:
        return []
    target = _TableSchema(
        table_id=target_table.id,
        schema_name=target_table.schema_name,
        table_name=target_table.table_name,
        columns=target_columns,
    )

    candidate_tables = list(
        session.scalars(
            select(Table)
            .where(
                Table.is_active.is_(True),
                Table.id != target_table_id,
            )
            .limit(5000)
        )
    )

    scored: list[tuple[float, list[str], list[str], _TableSchema]] = []
    for candidate_table in candidate_tables:
        columns = _fetch_table_columns(session, candidate_table, catalog_cache)
        if not columns:
            continue
        cand = _TableSchema(
            table_id=candidate_table.id,
            schema_name=candidate_table.schema_name,
            table_name=candidate_table.table_name,
            columns=columns,
        )
        score, overlap, cast = _score_candidate(target, cand)
        if score <= 0.0:
            continue
        scored.append((score, overlap, cast, cand))

    scored.sort(key=lambda item: item[0], reverse=True)
    scored = scored[: max(k, 0)]

    results: list[UnionScore] = []
    for score, overlap, cast, cand in scored:
        target_identifier = f"{target.schema_name}.{target.table_name}"
        candidate_identifier = f"{cand.schema_name}.{cand.table_name}"
        union_sql = _build_union_sql(
            target_identifier=target_identifier,
            target_cols=target.columns,
            other_identifier=candidate_identifier,
            other_cols=cand.columns,
            overlap_columns=overlap,
            cast_columns=cast,
        )
        rationale = _deterministic_rationale(target.columns, overlap, cast)
        if use_llm:
            try:
                try:
                    asyncio.get_running_loop()
                    # Already inside a running loop — skip the LLM path to avoid
                    # nested-loop errors and use the deterministic rationale.
                    pass
                except RuntimeError:
                    rationale = asyncio.run(
                        _maybe_llm_rationale(session, target, cand, overlap, cast, rationale)
                    )
            except Exception as exc:
                logger.debug("LLM rationale skipped: %s", exc)
        results.append(
            UnionScore(
                target_table_id=str(target.table_id),
                candidate_table_id=str(cand.table_id),
                score=score,
                overlap_columns=overlap,
                union_sql=union_sql,
                rationale=rationale,
                candidate_schema=candidate_identifier,
                candidate_table_name=cand.table_name,
                candidate_schema_name=cand.schema_name,
            )
        )

    return results


__all__ = ["UnionScore", "find_unionable"]
