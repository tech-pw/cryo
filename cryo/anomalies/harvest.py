"""Harvest table warehouse locations from enabled Cryo sources."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.anomalies.paths import LocationRow, normalize_warehouse_path
from cryo.db.models import Source, Table
from cryo.sources.factory import is_hive_source_type, source_catalog_for
from cryo.sources.hms_thrift import hms_uri_from_config, list_hive_table_locations_from_uri
from cryo.sources.locations import CatalogTableLocation

logger = logging.getLogger(__name__)


@dataclass
class HarvestedTable:
    source_id: uuid.UUID
    source_name: str
    source_type: str
    schema_name: str
    table_name: str
    location: str | None
    error: str | None = None
    table_id: uuid.UUID | None = None
    harvest_mode: str = "live"


@dataclass
class SourceHarvestSummary:
    source_id: uuid.UUID
    source_name: str
    source_type: str
    mode: str
    table_count: int
    skipped_count: int
    error: str | None = None


@dataclass
class HarvestResult:
    tables: list[HarvestedTable] = field(default_factory=list)
    sources: list[SourceHarvestSummary] = field(default_factory=list)

    def location_rows(self) -> list[LocationRow]:
        rows: list[LocationRow] = []
        for table in self.tables:
            if table.error or not table.location:
                continue
            if normalize_warehouse_path(table.location) is None:
                continue
            rows.append(
                LocationRow(
                    source_id=table.source_id,
                    source_name=table.source_name,
                    source_type=table.source_type,
                    schema_name=table.schema_name,
                    table_name=table.table_name,
                    location=table.location,
                    table_id=table.table_id,
                )
            )
        return rows


def harvest_all_sources(session: Session) -> HarvestResult:
    """Walk every enabled source. Never filter by ``maintenance_schema_names``.

    Live catalog metadata is the source of truth. If a source cannot be contacted,
    fall back to persisted ``tables.location`` for that source and mark ``db_fallback``.
    """
    sources = session.scalars(
        select(Source).where(Source.enabled.is_(True)).order_by(Source.name)
    ).all()
    table_ids = _table_id_index(session)
    result = HarvestResult()
    for source in sources:
        harvested, summary = _harvest_source(session, source, table_ids)
        result.tables.extend(harvested)
        result.sources.append(summary)
    return result


def _harvest_source(
    session: Session,
    source: Source,
    table_ids: dict[tuple[uuid.UUID, str, str], uuid.UUID],
) -> tuple[list[HarvestedTable], SourceHarvestSummary]:
    error: str | None = None
    mode = "live"
    locations: list[CatalogTableLocation]
    try:
        logger.info("Harvesting table locations from %s (%s)", source.name, source.type)
        locations = _live_table_locations(source)
    except Exception as exc:
        logger.exception("Live location harvest failed for source %s", source.name)
        error = str(exc)[:500]
        mode = "db_fallback"
        locations = _db_fallback_locations(session, source.id)

    harvested: list[HarvestedTable] = []
    skipped = 0
    with_location = 0
    for loc in locations:
        table_id = table_ids.get((source.id, loc.schema_name, loc.table_name))
        usable = bool(loc.location) and loc.error is None
        if loc.location and normalize_warehouse_path(loc.location) is None:
            usable = False
        if not usable:
            skipped += 1
        else:
            with_location += 1
        harvested.append(
            HarvestedTable(
                source_id=source.id,
                source_name=source.name,
                source_type=source.type,
                schema_name=loc.schema_name,
                table_name=loc.table_name,
                location=loc.location,
                error=loc.error,
                table_id=table_id,
                harvest_mode=mode,
            )
        )
    summary = SourceHarvestSummary(
        source_id=source.id,
        source_name=source.name,
        source_type=source.type,
        mode=mode,
        table_count=with_location,
        skipped_count=skipped,
        error=error,
    )
    return harvested, summary


def _live_table_locations(source: Source) -> list[CatalogTableLocation]:
    """Hive goes straight to HMS Thrift. Other catalogs use their SourceCatalog adapter."""
    if is_hive_source_type(source.type):
        return list_hive_table_locations_from_uri(hms_uri_from_config(source.config or {}))
    return source_catalog_for(source).list_table_locations()


def _db_fallback_locations(session: Session, source_id: uuid.UUID) -> list[CatalogTableLocation]:
    rows = session.execute(
        select(Table.schema_name, Table.table_name, Table.location).where(
            Table.source_id == source_id,
            Table.is_active.is_(True),
            Table.location.is_not(None),
        )
    ).all()
    return [
        CatalogTableLocation(
            schema_name=schema_name,
            table_name=table_name,
            location=location,
        )
        for schema_name, table_name, location in rows
    ]


def _table_id_index(
    session: Session,
) -> dict[tuple[uuid.UUID, str, str], uuid.UUID]:
    rows = session.execute(
        select(Table.id, Table.source_id, Table.schema_name, Table.table_name)
    ).all()
    return {
        (source_id, schema_name, table_name): table_id
        for table_id, source_id, schema_name, table_name in rows
    }
