"""Pick which tables a batch maintenance run works on, from stored scan stats only.

Eligibility is "the scanner says this table needs work": too many small files, or enough
delete files that reads are paying merge-on-read cost. Among eligible tables the order is a
weighted blend of three signals, each normalized to 0..1 so no single unit dominates:

- heat: Trino query volume over 30 days. Compacting a table nobody queries saves storage
  requests but no query time, and the point of the run is cheaper queries.
- need: how much of the table is actually in bad shape (small-file ratio, delete ratio).
- roi: small files eliminated per GB that has to be rewritten, plus projected monthly S3
  savings. A table with 50k small files in 2 GB is a far better use of a lane than one with
  the same count spread over 2 TB.

Heat and roi are rank-normalized rather than min-max scaled: one 25 TB table or one table
with a million queries would otherwise compress every other table into the bottom sliver.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from cryo.db.models import MaintenanceJob, Source, Table, TableStats
from cryo.domain.enums import JobStatus


@dataclass(frozen=True)
class SelectionWeights:
    heat: float = 0.45
    need: float = 0.35
    roi: float = 0.20


@dataclass(frozen=True)
class Candidate:
    table_id: uuid.UUID
    schema_name: str
    table_name: str
    total_size_gb: float
    total_files: int
    small_files: int
    delete_file_pct: float
    query_count_30d: int
    projected_savings_usd_month: float
    needs_compaction: bool

    @property
    def display_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"

    @property
    def small_file_ratio(self) -> float:
        return self.small_files / self.total_files if self.total_files > 0 else 0.0

    @property
    def rewrite_gb(self) -> float:
        """Rough bytes the compaction has to read: the small-file share of the table."""
        return self.total_size_gb * self.small_file_ratio


@dataclass(frozen=True)
class RankedCandidate:
    candidate: Candidate
    score: float
    heat: float
    need: float
    roi: float


def is_eligible(candidate: Candidate, *, min_delete_file_pct: float) -> bool:
    return candidate.needs_compaction or candidate.delete_file_pct >= min_delete_file_pct


def need_score(candidate: Candidate) -> float:
    delete_share = min(candidate.delete_file_pct / 20.0, 1.0)
    return min(1.0, 0.7 * candidate.small_file_ratio + 0.3 * delete_share)


def roi_raw(candidate: Candidate) -> float:
    files_per_gb = candidate.small_files / (1.0 + candidate.rewrite_gb)
    return math.log1p(files_per_gb) + math.log1p(max(candidate.projected_savings_usd_month, 0.0))


def _rank_normalize(values: Sequence[float]) -> list[float]:
    """Map each value to its percentile rank in 0..1; ties share the same rank."""
    if not values:
        return []
    if len(values) == 1:
        return [1.0]
    ordered = sorted(values)
    position: dict[float, float] = {}
    for index, value in enumerate(ordered):
        position.setdefault(value, index)
    top = len(values) - 1
    return [position[value] / top for value in values]


def rank_candidates(
    candidates: Sequence[Candidate],
    *,
    limit: int,
    weights: SelectionWeights | None = None,
    min_delete_file_pct: float = 1.0,
    require_eligible: bool = True,
) -> list[RankedCandidate]:
    """require_eligible=False is for an operator-picked table list: they asked for these
    tables, so the planner alone decides what (if anything) each one needs."""
    weights = weights or SelectionWeights()
    eligible = [
        c
        for c in candidates
        if not require_eligible or is_eligible(c, min_delete_file_pct=min_delete_file_pct)
    ]
    heats = _rank_normalize([math.log1p(c.query_count_30d) for c in eligible])
    rois = _rank_normalize([roi_raw(c) for c in eligible])
    ranked = [
        RankedCandidate(
            candidate=c,
            heat=heat,
            need=need_score(c),
            roi=roi,
            score=weights.heat * heat + weights.need * need_score(c) + weights.roi * roi,
        )
        for c, heat, roi in zip(eligible, heats, rois, strict=True)
    ]
    # table_id breaks score ties so the same stats always produce the same run.
    ranked.sort(key=lambda r: (-r.score, str(r.candidate.table_id)))
    return ranked[: max(limit, 0)]


def load_candidates(
    session: Session,
    *,
    now: datetime,
    cooldown_hours: float,
    max_stats_age_days: float,
    table_ids: Sequence[uuid.UUID] | None = None,
) -> list[Candidate]:
    """Active tables on enabled sources with usable, reasonably fresh stats.

    Excluded up front: tables whose last scan could not read them (maintenance would fail the
    same way), tables in a maintenance back-off window, tables maintained successfully within
    the cooldown, and schemas outside a source's maintenance allow-list when it has one.
    An explicit ``table_ids`` list (ad-hoc runs) skips the cooldown but keeps every safety
    exclusion.
    """
    recently_maintained = (
        select(MaintenanceJob.table_id)
        .where(
            MaintenanceJob.status.in_([JobStatus.SUCCEEDED.value, JobStatus.PARTIAL.value]),
            MaintenanceJob.finished_at >= now - timedelta(hours=cooldown_hours),
        )
        .distinct()
    )
    # Columns, not entities: a TableStats row carries the scanner's raw JSONB (partition lists,
    # file samples), and loading ~12k of them OOMKilled a 6 GiB api pod on 2026-09-24.
    stmt = (
        select(
            Table.id,
            Table.schema_name,
            Table.table_name,
            Source.maintenance_schema_names,
            TableStats.total_size_gb,
            TableStats.total_files,
            TableStats.small_files_count,
            TableStats.delete_file_pct,
            TableStats.query_count_30d,
            TableStats.projected_savings_usd_month,
            TableStats.needs_compaction,
        )
        .select_from(Table)
        .join(TableStats, TableStats.table_id == Table.id)
        .join(Source, Source.id == Table.source_id)
        .where(
            Table.is_active.is_(True),
            Source.enabled.is_(True),
            Table.last_scan_error_kind.is_(None),
            or_(Table.maintenance_retry_after.is_(None), Table.maintenance_retry_after <= now),
            TableStats.scan_at >= now - timedelta(days=max_stats_age_days),
        )
    )
    if table_ids:
        stmt = stmt.where(Table.id.in_(list(table_ids)))
    else:
        stmt = stmt.where(Table.id.not_in(recently_maintained))

    candidates: list[Candidate] = []
    for row in session.execute(stmt).all():
        allowed_schemas = row.maintenance_schema_names or []
        if allowed_schemas and row.schema_name not in allowed_schemas:
            continue
        candidates.append(
            Candidate(
                table_id=row.id,
                schema_name=row.schema_name,
                table_name=row.table_name,
                total_size_gb=float(row.total_size_gb or 0.0),
                total_files=int(row.total_files or 0),
                small_files=int(row.small_files_count or 0),
                delete_file_pct=float(row.delete_file_pct or 0.0),
                query_count_30d=int(row.query_count_30d or 0),
                projected_savings_usd_month=float(row.projected_savings_usd_month or 0.0),
                needs_compaction=bool(row.needs_compaction),
            )
        )
    return candidates
