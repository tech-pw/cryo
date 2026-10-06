"""Global catalog search — bounded literal matching over fleet metadata."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from cryo.ai.embedding_cache import get_or_compute_embedding
from cryo.ai.embeddings import cosine_similarity, embed_text
from cryo.api.deps import SessionDep, require_viewer
from cryo.db.model_utils import catalog_name_for_source, qualified_name
from cryo.db.models import Source, Table, TableColumn, TableStats
from cryo.db.tenant_scope import require_tenant, scope_by_tenant

router = APIRouter(prefix="/api/v1", tags=["search"])

SearchGroupName = Literal["catalogs", "schemas", "tables", "columns"]
MatchReason = Literal["name", "schema", "semantic", "column"]
_SEMANTIC_MIN_SIMILARITY = 0.35


class SearchMatchRead(BaseModel):
    asset_type: Literal["catalog", "schema", "table", "column"]
    name: str
    qualified_name: str | None = None
    catalog_id: UUID | None = None
    catalog_name: str | None = None
    schema_name: str | None = None
    table_id: UUID | None = None
    column_name: str | None = None
    data_type: str | None = None
    match_reason: MatchReason
    score: float | None = Field(
        default=None,
        description="Cosine similarity for semantic matches; omitted for literal matches.",
    )


class SearchGroupRead(BaseModel):
    asset_type: SearchGroupName
    total: int
    hits: list[SearchMatchRead]


class SearchResponse(BaseModel):
    query: str
    groups: list[SearchGroupRead]


class _MatchPriority(StrEnum):
    NAME = "name"
    SCHEMA = "schema"
    COLUMN = "column"
    SEMANTIC = "semantic"


_PRIORITY_ORDER = {
    _MatchPriority.NAME: 0,
    _MatchPriority.SCHEMA: 1,
    _MatchPriority.COLUMN: 2,
    _MatchPriority.SEMANTIC: 3,
}


def escape_ilike_pattern(query: str) -> str:
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def ilike_pattern(query: str) -> str:
    return f"%{escape_ilike_pattern(query.strip())}%"


def table_literal_match_reason(
    *,
    query: str,
    schema_name: str,
    table_name: str,
    qualified: str,
) -> MatchReason | None:
    """Return the strongest literal match reason for a table row, or None."""
    needle = query.strip().lower()
    if not needle:
        return None
    if needle in table_name.lower():
        return "name"
    if needle in schema_name.lower():
        return "schema"
    if needle in qualified.lower():
        return "name"
    return None


def merge_table_matches(
    *groups: list[SearchMatchRead],
) -> list[SearchMatchRead]:
    """Deduplicate table hits by table_id, keeping the strongest match reason."""
    merged: dict[UUID, SearchMatchRead] = {}
    for hits in groups:
        for hit in hits:
            if hit.table_id is None:
                continue
            existing = merged.get(hit.table_id)
            if existing is None:
                merged[hit.table_id] = hit
                continue
            existing_rank = _PRIORITY_ORDER[_MatchPriority(existing.match_reason)]
            new_rank = _PRIORITY_ORDER[_MatchPriority(hit.match_reason)]
            if new_rank < existing_rank:
                merged[hit.table_id] = hit
            elif new_rank == existing_rank and hit.match_reason == "semantic":
                existing_score = existing.score or 0.0
                new_score = hit.score or 0.0
                if new_score > existing_score:
                    merged[hit.table_id] = hit
    return sorted(
        merged.values(),
        key=lambda hit: (
            _PRIORITY_ORDER[_MatchPriority(hit.match_reason)],
            -(hit.score or 0.0),
            hit.name,
        ),
    )


def search_semantic_tables(
    session: Session,
    *,
    query: str,
    fleet_rows: list[dict[str, object]],
    min_similarity: float = _SEMANTIC_MIN_SIMILARITY,
    limit: int,
    table_by_id: dict[UUID, Table],
) -> list[SearchMatchRead]:
    query_vec = embed_text(query)
    hits: list[SearchMatchRead] = []
    for row in fleet_rows:
        table_id_raw = row.get("table_id")
        if not table_id_raw:
            continue
        table_id = UUID(str(table_id_raw))
        table = table_by_id.get(table_id)
        if table is None:
            continue
        vec = get_or_compute_embedding(session, row)
        similarity = cosine_similarity(query_vec, vec)
        if similarity < min_similarity:
            continue
        catalog = catalog_name_for_source(table.source)
        hits.append(
            SearchMatchRead(
                asset_type="table",
                name=table.table_name,
                qualified_name=qualified_name(catalog, table.schema_name, table.table_name),
                catalog_id=table.source_id,
                catalog_name=catalog,
                schema_name=table.schema_name,
                table_id=table.id,
                match_reason="semantic",
                score=similarity,
            )
        )
    hits.sort(key=lambda hit: hit.score or 0.0, reverse=True)
    return hits[:limit]


def _count_rows(session: Session, stmt: Select[Any]) -> int:
    counted = select(func.count()).select_from(stmt.order_by(None).subquery())
    return int(session.scalar(counted) or 0)


def _literal_catalog_hits(
    session: Session,
    pattern: str,
    tenant_id: UUID,
    *,
    offset: int,
    limit: int,
) -> tuple[list[SearchMatchRead], int]:
    stmt = scope_by_tenant(
        select(Source).where(Source.name.ilike(pattern, escape="\\")).order_by(Source.name),
        Source,
        tenant_id,
    )
    total = _count_rows(session, stmt)
    sources = session.scalars(stmt.offset(offset).limit(limit)).all()
    hits = [
        SearchMatchRead(
            asset_type="catalog",
            name=source.name,
            catalog_id=source.id,
            catalog_name=source.name,
            match_reason="name",
        )
        for source in sources
    ]
    return hits, total


def _literal_schema_hits(
    session: Session,
    pattern: str,
    tenant_id: UUID,
    *,
    offset: int,
    limit: int,
) -> tuple[list[SearchMatchRead], int]:
    stmt: Select[tuple[UUID, str, str]] = (
        select(Table.source_id, Table.schema_name, Source.name)
        .join(Source, Table.source_id == Source.id)
        .where(Table.schema_name.ilike(pattern, escape="\\"))
        .distinct()
        .order_by(Source.name, Table.schema_name)
    )
    stmt = scope_by_tenant(stmt, Table, tenant_id)
    total = _count_rows(session, stmt)
    rows = session.execute(stmt.offset(offset).limit(limit)).all()
    hits = [
        SearchMatchRead(
            asset_type="schema",
            name=schema_name,
            qualified_name=f"{catalog_name}.{schema_name}",
            catalog_id=source_id,
            catalog_name=catalog_name,
            schema_name=schema_name,
            match_reason="schema",
        )
        for source_id, schema_name, catalog_name in rows
    ]
    return hits, total


def _literal_table_hits(
    session: Session,
    *,
    query: str,
    pattern: str,
    tenant_id: UUID,
    offset: int,
    limit: int,
) -> tuple[list[SearchMatchRead], int]:
    stmt = (
        select(Table)
        .join(Source, Table.source_id == Source.id)
        .where(
            or_(
                Table.table_name.ilike(pattern, escape="\\"),
                Table.schema_name.ilike(pattern, escape="\\"),
                func.concat(Source.name, ".", Table.schema_name, ".", Table.table_name).ilike(
                    pattern, escape="\\"
                ),
            )
        )
        .order_by(Source.name, Table.schema_name, Table.table_name)
    )
    stmt = scope_by_tenant(stmt, Table, tenant_id)
    total = _count_rows(session, stmt)
    tables = session.scalars(
        stmt.options(selectinload(Table.source)).offset(offset).limit(limit)
    ).all()
    hits: list[SearchMatchRead] = []
    for table in tables:
        catalog = catalog_name_for_source(table.source)
        qualified = qualified_name(catalog, table.schema_name, table.table_name)
        reason = table_literal_match_reason(
            query=query,
            schema_name=table.schema_name,
            table_name=table.table_name,
            qualified=qualified,
        )
        hits.append(
            SearchMatchRead(
                asset_type="table",
                name=table.table_name,
                qualified_name=qualified,
                catalog_id=table.source_id,
                catalog_name=catalog,
                schema_name=table.schema_name,
                table_id=table.id,
                match_reason=reason or "name",
            )
        )
    return hits, total


def _literal_column_hits(
    session: Session,
    pattern: str,
    tenant_id: UUID,
    *,
    offset: int,
    limit: int,
) -> tuple[list[SearchMatchRead], int]:
    stmt = (
        select(TableColumn, Table)
        .join(Table, TableColumn.table_id == Table.id)
        .join(Source, Table.source_id == Source.id)
        .where(
            or_(
                TableColumn.column_name.ilike(pattern, escape="\\"),
                TableColumn.description.ilike(pattern, escape="\\"),
            )
        )
        .order_by(
            Source.name,
            Table.schema_name,
            Table.table_name,
            TableColumn.column_name,
        )
    )
    stmt = scope_by_tenant(stmt, Table, tenant_id)
    total = _count_rows(session, stmt)
    rows = session.execute(
        stmt.options(selectinload(Table.source)).offset(offset).limit(limit)
    ).all()
    hits: list[SearchMatchRead] = []
    for column, table in rows:
        catalog = catalog_name_for_source(table.source)
        qualified = qualified_name(catalog, table.schema_name, table.table_name)
        hits.append(
            SearchMatchRead(
                asset_type="column",
                name=column.column_name,
                qualified_name=f"{qualified}.{column.column_name}",
                catalog_id=table.source_id,
                catalog_name=catalog,
                schema_name=table.schema_name,
                table_id=table.id,
                column_name=column.column_name,
                data_type=column.data_type,
                match_reason="column",
            )
        )
    return hits, total


def _fleet_rows(
    session: Session, tenant_id: UUID
) -> tuple[list[dict[str, object]], dict[UUID, Table]]:
    stmt = (
        select(Table, TableStats)
        .join(TableStats, TableStats.table_id == Table.id)
        .options(selectinload(Table.source))
        .where(Table.is_active.is_(True))
    )
    stmt = scope_by_tenant(stmt, Table, tenant_id)
    rows = session.execute(stmt).all()
    fleet_rows: list[dict[str, object]] = []
    table_by_id: dict[UUID, Table] = {}
    for table, stats in rows:
        table_by_id[table.id] = table
        fleet_rows.append(
            {
                "table_id": str(table.id),
                "schema_name": table.schema_name,
                "table_name": table.table_name,
                "stats": {
                    "total_files": stats.total_files,
                    "small_files_count": stats.small_files_count,
                    "grade": stats.grade,
                    "partition_col": stats.partition_col,
                    "overall_score": stats.overall_score,
                },
            }
        )
    return fleet_rows, table_by_id


@router.get("/search", response_model=SearchResponse)
def search_assets(
    session: SessionDep,
    _principal: Annotated[dict, Depends(require_viewer)],
    q: Annotated[str, Query(min_length=1, description="Search query")],
    limit: Annotated[int, Query(ge=1, le=100, description="Max hits per group")] = 8,
    group: Annotated[
        SearchGroupName | None,
        Query(description="Return only this asset group (paginated)"),
    ] = None,
    offset: Annotated[int, Query(ge=0, description="Pagination offset within a group")] = 0,
) -> SearchResponse:
    """Search catalogs, schemas, tables, and columns by name.

    Literal SQL only, each group limited in the database. A full-fleet embedding
    scan loads every ``TableStats.raw`` blob and OOM-kills the API on a real fleet.
    """
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="q must not be blank")

    tenant_id = require_tenant(session)
    pattern = ilike_pattern(query)
    page_offset = offset if group is not None else 0

    def empty() -> tuple[list[SearchMatchRead], int]:
        return [], 0

    catalog_hits, catalog_total = (
        _literal_catalog_hits(session, pattern, tenant_id, offset=page_offset, limit=limit)
        if group in {None, "catalogs"}
        else empty()
    )
    schema_hits, schema_total = (
        _literal_schema_hits(session, pattern, tenant_id, offset=page_offset, limit=limit)
        if group in {None, "schemas"}
        else empty()
    )
    table_hits, table_total = (
        _literal_table_hits(
            session,
            query=query,
            pattern=pattern,
            tenant_id=tenant_id,
            offset=page_offset,
            limit=limit,
        )
        if group in {None, "tables"}
        else empty()
    )
    column_hits, column_total = (
        _literal_column_hits(session, pattern, tenant_id, offset=page_offset, limit=limit)
        if group in {None, "columns"}
        else empty()
    )

    selected: dict[SearchGroupName, tuple[list[SearchMatchRead], int]] = {
        "catalogs": (catalog_hits, catalog_total),
        "schemas": (schema_hits, schema_total),
        "tables": (table_hits, table_total),
        "columns": (column_hits, column_total),
    }
    if group is not None:
        hits, total = selected[group]
        return SearchResponse(
            query=query,
            groups=[SearchGroupRead(asset_type=group, total=total, hits=hits)],
        )

    return SearchResponse(
        query=query,
        groups=[
            SearchGroupRead(asset_type=name, total=total, hits=hits)
            for name, (hits, total) in selected.items()
        ],
    )
