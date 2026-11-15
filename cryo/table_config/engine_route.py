"""Route table-property commits on Iceberg format-version 3 tables through an engine (#557).

PyIceberg (pinned 0.11.x) raises ``NotImplementedError: Writing V3 is not yet supported`` on
any commit to a v3 table, including a metadata-only property change
(apache/iceberg-python#1551). For v3 tables Cryo runs the change as engine DDL instead:

- Spark: ``ALTER TABLE ... SET TBLPROPERTIES`` / ``UNSET TBLPROPERTIES IF EXISTS`` (any key).
- Trino: ``ALTER TABLE ... SET PROPERTIES`` only for keys its Iceberg connector maps
  (``cryo.engines.trino.sql.TRINO_ICEBERG_TABLE_PROPERTIES``), and never for removals.

v1/v2 tables keep the PyIceberg path. Setting ``CRYO_PYICEBERG_V3_WRITES=true`` sends v3
tables back to PyIceberg once Cryo pins a release that writes v3 metadata.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import Destination, Source, Table, TableStats
from cryo.domain.enums import EngineType, OperationType

PYICEBERG_ROUTE = "pyiceberg"
ENGINE_ROUTE = "engine"

NO_ROUTE_MESSAGE = (
    "Property changes on Iceberg v3 tables need a Spark destination (or Trino, for the keys "
    "Trino can set). PyIceberg can't write v3 metadata yet (apache/iceberg-python#1551)."
)


class NoPropertyRouteError(Exception):
    """A v3 table has no enabled destination that can apply the requested change."""

    def __init__(self, message: str = NO_ROUTE_MESSAGE) -> None:
        super().__init__(message)


class EnginePropertyApplyError(Exception):
    def __init__(self, message: str, *, engine: str | None = None) -> None:
        super().__init__(message)
        self.engine = engine


@dataclass(frozen=True)
class PropertyRoute:
    engine: EngineType
    destination_id: uuid.UUID
    destination_name: str
    catalog: str
    config: Mapping[str, Any]

    def audit_detail(self) -> str:
        return (
            f"route={ENGINE_ROUTE} engine={self.engine.value} destination={self.destination_name}"
        )


def parse_format_version(value: Any) -> int | None:
    try:
        return int(str(value).strip()) if value is not None and str(value).strip() else None
    except ValueError:
        return None


def stored_format_version(stats: TableStats | None) -> int | None:
    if stats is None:
        return None
    return parse_format_version((stats.raw or {}).get("format_version"))


def pyiceberg_can_commit(format_version: int | None, target_version: int | None = None) -> bool:
    """True when PyIceberg can make this commit. Unknown versions keep the PyIceberg path."""
    from cryo.config import get_settings

    highest = max(v for v in (format_version, target_version, 0) if v is not None)
    if highest < 3:
        return True
    return bool(get_settings().pyiceberg_v3_writes)


def _engine_of(destination: Destination) -> EngineType | None:
    try:
        return EngineType(str(destination.engine))
    except ValueError:
        return None


def _capable(
    destination: Destination,
    source: Source,
    set_keys: Sequence[str],
    remove_keys: Sequence[str],
) -> bool:
    from cryo.engines.trino.catalog_bindings import destination_serves_source
    from cryo.engines.trino.sql import trino_supports_table_properties

    if not destination.enabled:
        return False
    engine = _engine_of(destination)
    if engine is EngineType.SPARK_EMR:
        return True
    if engine is EngineType.TRINO:
        return (
            not remove_keys
            and bool(set_keys)
            and trino_supports_table_properties(set_keys)
            and destination_serves_source(engine, destination.config, source.id)
        )
    return False


def _spark_catalog(source: Source, destination: Destination) -> str | None:
    catalog = (source.config or {}).get("catalog_name") or (destination.config or {}).get("catalog")
    return catalog if isinstance(catalog, str) and catalog else None


def _route_for(destination: Destination, source: Source) -> PropertyRoute | None:
    from cryo.db.model_utils import catalog_name_for_source
    from cryo.engines.trino.catalog_bindings import resolve_trino_catalog

    engine = _engine_of(destination)
    if engine is EngineType.SPARK_EMR:
        from cryo.governor.native_admit import _fallback_engine_config

        catalog = _spark_catalog(source, destination)
        config = _fallback_engine_config(destination.config or {}, source.config or {})
    elif engine is EngineType.TRINO:
        catalog = resolve_trino_catalog(
            destination.config,
            source_id=source.id,
            source_catalog_name=catalog_name_for_source(source),
        )
        config = dict(destination.config or {})
    else:
        return None
    if not catalog:
        return None
    config.setdefault("destination_id", str(destination.id))
    return PropertyRoute(
        engine=engine,
        destination_id=destination.id,
        destination_name=destination.name,
        catalog=catalog,
        config=config,
    )


def choose_route(
    source: Source,
    candidates: Iterable[Destination],
    set_keys: Sequence[str],
    remove_keys: Sequence[str],
) -> PropertyRoute | None:
    """First capable candidate, in the order given."""
    for destination in candidates:
        if _capable(destination, source, set_keys, remove_keys):
            route = _route_for(destination, source)
            if route is not None:
                return route
    return None


def candidate_destinations(session: Session, table: Table) -> list[Destination]:
    """The effective preset's destination, its SQL fallback, then every other enabled
    destination with Spark ahead of Trino."""
    from cryo.db.repositories import presets as preset_repo

    ordered: list[Destination] = []
    effective = preset_repo.resolve_effective_preset(session, table)
    if effective is not None:
        primary = session.get(Destination, uuid.UUID(effective.preset.destination_id))
        if primary is not None:
            ordered.append(primary)
            config = primary.config or {}
            fallback_id = config.get("sql_fallback_destination_id") or config.get(
                "fallback_destination_id"
            )
            if fallback_id:
                try:
                    fallback = session.get(Destination, uuid.UUID(str(fallback_id)))
                except ValueError:
                    fallback = None
                if fallback is not None:
                    ordered.append(fallback)
    rest = session.scalars(
        select(Destination)
        .where(Destination.enabled.is_(True))
        .where(Destination.engine.in_([EngineType.SPARK_EMR.value, EngineType.TRINO.value]))
        .order_by(Destination.name)
    ).all()
    seen = {d.id for d in ordered}
    spark_first = sorted(rest, key=lambda d: (d.engine != EngineType.SPARK_EMR.value, d.name))
    ordered.extend(d for d in spark_first if d.id not in seen)
    return ordered


def resolve_property_route(
    session: Session,
    table: Table,
    set_keys: Sequence[str],
    remove_keys: Sequence[str] = (),
) -> PropertyRoute | None:
    source = session.get(Source, table.source_id) if table.source_id else None
    if source is None:
        return None
    return choose_route(source, candidate_destinations(session, table), set_keys, remove_keys)


def render_statements(
    route: PropertyRoute,
    schema: str,
    table: str,
    set_properties: Mapping[str, str],
    remove_properties: Sequence[str],
) -> list[str]:
    if route.engine is EngineType.SPARK_EMR:
        from cryo.engines.spark import sql as spark_sql

        out: list[str] = []
        if set_properties:
            out.append(
                spark_sql.render_set_table_properties(
                    route.catalog, schema, table, dict(set_properties)
                )
            )
        if remove_properties:
            out.append(
                spark_sql.render_unset_table_properties(
                    route.catalog, schema, table, remove_properties
                )
            )
        return out
    from cryo.engines.trino import sql as trino_sql

    if remove_properties:
        raise EnginePropertyApplyError("Trino cannot remove Iceberg table properties")
    return [
        trino_sql.render_set_table_properties(route.catalog, schema, table, dict(set_properties))
    ]


def apply_via_engine(
    route: PropertyRoute,
    schema: str,
    table: str,
    set_properties: Mapping[str, str],
    remove_properties: Sequence[str] = (),
) -> list[str]:
    """Run the DDL on the route's destination through the engine's own ``execute``. Returns
    the statements run; raises ``EnginePropertyApplyError`` on the first failure."""
    from cryo.engines.base import RenderedStatement
    from cryo.engines.factory import create_engine

    statements = render_statements(route, schema, table, set_properties, remove_properties)
    engine = create_engine(route.engine, route.config)
    for sql in statements:
        # The engines key retries, sizing and metrics off an OperationType. A property change
        # is metadata-only like a manifest rewrite, so it carries that type; nothing here
        # reads procedure metrics from it.
        stmt = RenderedStatement(
            action=OperationType.OPTIMIZE_MANIFESTS,
            sql=sql,
            engine=route.engine,
            metadata={"catalog": route.catalog, "statement_kind": "table_properties"},
        )
        result = engine.execute(stmt)
        if not result.ok:
            raise EnginePropertyApplyError(
                f"{route.engine.value} destination {route.destination_name} failed: "
                f"{result.error or 'unknown error'}",
                engine=route.engine.value,
            )
    return statements


@dataclass(frozen=True)
class PropertyRouteStatus:
    format_version: int | None
    route: str  # "pyiceberg" | "engine" | "none"
    engine: str | None = None
    destination_name: str | None = None
    message: str | None = None

    @property
    def can_apply(self) -> bool:
        return self.route != "none"

    def as_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "route": self.route,
            "engine": self.engine,
            "destination_name": self.destination_name,
            "message": self.message,
        }


def property_route_status(
    session: Session,
    table: Table,
    set_keys: Sequence[str],
    remove_keys: Sequence[str] = (),
    *,
    format_version: int | None = None,
) -> PropertyRouteStatus:
    """Where a change to these keys would go, without running anything.

    With no keys it asks for a route that takes any key, which only Spark can promise.
    """
    version = (
        format_version
        if format_version is not None
        else stored_format_version(session.get(TableStats, table.id))
    )
    if pyiceberg_can_commit(version):
        return PropertyRouteStatus(format_version=version, route=PYICEBERG_ROUTE)
    if set_keys or remove_keys:
        route = resolve_property_route(session, table, set_keys, remove_keys)
    else:
        route = resolve_property_route(session, table, (), ANY_KEY)
    if route is None:
        return PropertyRouteStatus(format_version=version, route="none", message=NO_ROUTE_MESSAGE)
    return PropertyRouteStatus(
        format_version=version,
        route=ENGINE_ROUTE,
        engine=route.engine.value,
        destination_name=route.destination_name,
        message=(
            f"Iceberg v{version} table: Cryo applies property changes with "
            f"{route.engine.value} ({route.destination_name}) because PyIceberg can't write "
            "v3 metadata yet."
        ),
    )


# A removal Trino can't do, so only a Spark destination matches it.
ANY_KEY: tuple[str, ...] = ("*",)
