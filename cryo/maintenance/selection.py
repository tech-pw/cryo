"""Pick which tables a batch maintenance run works on, from stored scan stats only.

Eligibility is "the scanner says this table needs work": too many small files, or enough
delete files that reads are paying merge-on-read cost. Eligible tables are then ranked and
cut to a compute budget exactly as AutoComp (Gruenheid, Camacho-Rodríguez et al.,
arXiv:2504.04186) specifies for its resource-constrained scenario:

- T1, file-count reduction (§4.2): ΔF_c = files in the candidate smaller than the rewrite
  target. Delete files are files of the candidate below target that the rewrite removes, so
  they count too.
- T2, compute cost (§4.2): GBHr_c = ExecutorMemoryGB × DataSize_c / RewriteBytesPerHour, with
  DataSize_c the bytes of the below-target files (what a bin-pack rewrite reads).
- Min-max normalize each trait over the candidate pool, S_c = w1·T1' − w2·T2' with the
  paper's evaluation weights 0.7 / 0.3 (§4.3, §6), rank by S_c descending, then greedily take
  the highest-ranked candidates that fit the GBHr budget (§4.3).

Candidates are whole tables, as in the paper's production deployment (§5); the planner then
orders partitions inside a table by small-file count.

``heat_weight`` is a Cryo extension, not part of the paper (which lists workload awareness as
future work, §7): S = (1 − h)·S_c + h·H', where H' is min-max normalized log(1 + Trino
queries over 30 days). Compacting a table nobody queries saves storage requests but no query
time. ``autocomp_trace`` records both orderings so the two can be compared run over run.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from cryo.db.models import MaintenanceJob, Source, Table, TableStats
from cryo.domain.enums import JobStatus

_GIB = 1024**3


@dataclass(frozen=True)
class AutoCompParams:
    w_file_reduction: float = 0.7
    w_compute_cost: float = 0.3
    heat_weight: float = 0.3
    executor_memory_gb: float = 16.0
    rewrite_gb_per_hour: float = 25.0
    # A table runs on one lane, so one run can spend at most one lane's GB·hours on it. Tables
    # needing more are capped (the planner then rewrites their worst partitions first) instead
    # of being discarded as over budget every night.
    max_table_gbhr: float | None = None
    # Replaces rewrite_data_gb / rewrite_gb_per_hour as the hours estimate (Ernest model).
    duration_hours: Callable[[Candidate], float] | None = None

    @property
    def max_table_rewrite_gb(self) -> float | None:
        if self.max_table_gbhr is None:
            return None
        return self.max_table_gbhr * self.rewrite_gb_per_hour / self.executor_memory_gb


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
    delete_files: int = 0
    # None when the table was last scanned before the scanner measured it.
    files_below_target: int | None = None
    bytes_below_target: int | None = None

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

    @property
    def measured_below_target(self) -> bool:
        return self.files_below_target is not None and self.bytes_below_target is not None

    @property
    def file_count_reduction(self) -> int:
        below = self.files_below_target if self.files_below_target is not None else self.small_files
        return below + self.delete_files

    @property
    def rewrite_data_gb(self) -> float:
        if self.bytes_below_target is not None:
            return self.bytes_below_target / _GIB
        return self.rewrite_gb


@dataclass(frozen=True)
class RankedCandidate:
    candidate: Candidate
    score: float
    t1: float
    t2: float
    heat: float
    s_c: float
    gbhr: float
    capped: bool = False


def is_eligible(candidate: Candidate, *, min_delete_file_pct: float) -> bool:
    return candidate.needs_compaction or candidate.delete_file_pct >= min_delete_file_pct


def compute_cost_gbhr(candidate: Candidate, params: AutoCompParams) -> float:
    hours = (
        params.duration_hours(candidate)
        if params.duration_hours is not None
        else candidate.rewrite_data_gb / params.rewrite_gb_per_hour
    )
    gbhr = params.executor_memory_gb * hours
    if params.max_table_gbhr is not None:
        return min(gbhr, params.max_table_gbhr)
    return gbhr


def min_max(values: Sequence[float]) -> list[float]:
    """T' = (T − min) / (max − min). A trait every candidate ties on carries no ranking
    information, so it normalizes to 0 for all of them."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi <= lo:
        return [0.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def rank_candidates(
    candidates: Sequence[Candidate],
    *,
    limit: int,
    params: AutoCompParams | None = None,
    min_delete_file_pct: float = 1.0,
    require_eligible: bool = True,
) -> list[RankedCandidate]:
    """require_eligible=False is for an operator-picked table list: they asked for these
    tables, so the planner alone decides what (if anything) each one needs."""
    params = params or AutoCompParams()
    eligible = [
        c
        for c in candidates
        if not require_eligible or is_eligible(c, min_delete_file_pct=min_delete_file_pct)
    ]
    costs = [compute_cost_gbhr(c, params) for c in eligible]
    t1s = min_max([float(c.file_count_reduction) for c in eligible])
    t2s = min_max(costs)
    heats = min_max([math.log1p(c.query_count_30d) for c in eligible])
    h = params.heat_weight
    ranked = []
    for c, t1, t2, heat, gbhr in zip(eligible, t1s, t2s, heats, costs, strict=True):
        s_c = params.w_file_reduction * t1 - params.w_compute_cost * t2
        ranked.append(
            RankedCandidate(
                candidate=c,
                score=(1.0 - h) * s_c + h * heat,
                t1=t1,
                t2=t2,
                heat=heat,
                s_c=s_c,
                gbhr=gbhr,
                capped=(
                    (cap := params.max_table_rewrite_gb) is not None and c.rewrite_data_gb > cap
                ),
            )
        )
    # table_id breaks score ties so the same stats always produce the same run.
    ranked.sort(key=lambda r: (-r.score, str(r.candidate.table_id)))
    return ranked[: max(limit, 0)]


def fit_budget(
    ranked: Sequence[RankedCandidate], budget_gbhr: float | None
) -> tuple[list[RankedCandidate], list[RankedCandidate]]:
    """AutoComp's greedy top-k: walk the ranking and keep every candidate that still fits.
    A candidate too big for what is left is skipped, not a stopping point."""
    if budget_gbhr is None:
        return list(ranked), []
    kept: list[RankedCandidate] = []
    over: list[RankedCandidate] = []
    remaining = budget_gbhr
    for item in ranked:
        if item.gbhr <= remaining:
            kept.append(item)
            remaining -= item.gbhr
        else:
            over.append(item)
    return kept, over


def autocomp_trace(
    candidates: Sequence[Candidate],
    chosen: Sequence[RankedCandidate],
    *,
    limit: int,
    params: AutoCompParams,
    budget_gbhr: float | None,
    min_delete_file_pct: float = 1.0,
) -> dict[str, Any]:
    """What the paper-only ranking (heat_weight = 0) would have picked next to what ran."""
    paper_params = replace(params, heat_weight=0.0)
    paper, _ = fit_budget(
        rank_candidates(
            candidates, limit=limit, params=paper_params, min_delete_file_pct=min_delete_file_pct
        ),
        budget_gbhr,
    )
    chosen_ids = {r.candidate.table_id for r in chosen}
    paper_ids = {r.candidate.table_id for r in paper}
    union = chosen_ids | paper_ids
    return {
        "weights": {
            "w_file_reduction": round(params.w_file_reduction, 4),
            "w_compute_cost": round(params.w_compute_cost, 4),
            "heat_weight": round(params.heat_weight, 4),
        },
        "executor_memory_gb": params.executor_memory_gb,
        "rewrite_gb_per_hour": round(params.rewrite_gb_per_hour, 3),
        "budget_gbhr": None if budget_gbhr is None else round(budget_gbhr, 3),
        "chosen_gbhr": round(sum(r.gbhr for r in chosen), 3),
        "measured_below_target": sum(1 for r in chosen if r.candidate.measured_below_target),
        "capped": sum(1 for r in chosen if r.capped),
        "chosen": len(chosen_ids),
        "paper_only": len(paper_ids),
        "overlap": len(chosen_ids & paper_ids),
        "jaccard": round(len(chosen_ids & paper_ids) / len(union), 4) if union else 1.0,
        "paper_only_missed": sorted(str(i) for i in paper_ids - chosen_ids),
    }


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
            TableStats.total_delete_files,
            # Byte counts overflow a 32-bit int cast; a double is exact far past any table here.
            TableStats.raw["files_below_target"].as_float().label("files_below_target"),
            TableStats.raw["bytes_below_target"].as_float().label("bytes_below_target"),
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
                delete_files=int(row.total_delete_files or 0),
                files_below_target=_optional_int(row.files_below_target),
                bytes_below_target=_optional_int(row.bytes_below_target),
            )
        )
    return candidates


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)
