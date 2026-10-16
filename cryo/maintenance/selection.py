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

import json
import logging
import math
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from cryo.db.models import MaintenanceJob, Source, Table, TableStats
from cryo.db.repositories.exclusions import table_is_excluded
from cryo.domain.enums import JobStatus
from cryo.maintenance.skip_history import (
    REASON_BACKOFF,
    REASON_COOLDOWN,
    REASON_EXCLUDED,
    REASON_STALE_STATS,
    clear_pending,
    engine_id_for,
    remember_budget,
    remember_loaded,
    remember_ranked,
)

logger = logging.getLogger(__name__)

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


# rank_candidates' default. Nightly selection does not pass a different bar.
DEFAULT_MIN_DELETE_FILE_PCT = 1.0
# Statuses that start the nightly cooldown. load_candidates and the status endpoint share this.
COOLDOWN_JOB_STATUSES: tuple[str, ...] = (
    JobStatus.SUCCEEDED.value,
    JobStatus.PARTIAL.value,
)


def has_compaction_or_delete_work(
    *,
    needs_compaction: bool,
    delete_file_pct: float,
    min_delete_file_pct: float,
) -> bool:
    return needs_compaction or delete_file_pct >= min_delete_file_pct


def is_eligible(candidate: Candidate, *, min_delete_file_pct: float) -> bool:
    return has_compaction_or_delete_work(
        needs_compaction=candidate.needs_compaction,
        delete_file_pct=candidate.delete_file_pct,
        min_delete_file_pct=min_delete_file_pct,
    )


def schema_in_maintenance_allow_list(
    schema_name: str, maintenance_schema_names: Sequence[str] | None
) -> bool:
    """Empty or missing allow-list means every schema is in scope."""
    allowed = maintenance_schema_names or []
    return not allowed or schema_name in allowed


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
    min_delete_file_pct: float = DEFAULT_MIN_DELETE_FILE_PCT,
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
    chosen = ranked[: max(limit, 0)]
    _remember_ranked(
        candidates,
        chosen,
        min_delete_file_pct=min_delete_file_pct,
        require_eligible=require_eligible,
    )
    return chosen


def fit_budget(
    ranked: Sequence[RankedCandidate], budget_gbhr: float | None
) -> tuple[list[RankedCandidate], list[RankedCandidate]]:
    """AutoComp's greedy top-k: walk the ranking and keep every candidate that still fits.
    A candidate too big for what is left is skipped, not a stopping point."""
    kept: list[RankedCandidate]
    over: list[RankedCandidate]
    if budget_gbhr is None:
        kept, over = list(ranked), []
    else:
        kept = []
        over = []
        remaining = budget_gbhr
        for item in ranked:
            if item.gbhr <= remaining:
                kept.append(item)
                remaining -= item.gbhr
            else:
                over.append(item)
    _remember_budget(ranked, kept, over)
    return kept, over


def autocomp_trace(
    candidates: Sequence[Candidate],
    chosen: Sequence[RankedCandidate],
    *,
    limit: int,
    params: AutoCompParams,
    budget_gbhr: float | None,
    min_delete_file_pct: float = DEFAULT_MIN_DELETE_FILE_PCT,
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
    the cooldown, schemas outside a source's maintenance allow-list when it has one, and
    schemas/tables an operator excluded. An explicit ``table_ids`` list (ad-hoc runs) skips
    the cooldown but keeps every safety exclusion.
    """
    recently_maintained = (
        select(MaintenanceJob.table_id)
        .where(
            MaintenanceJob.status.in_(COOLDOWN_JOB_STATUSES),
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
            ~table_is_excluded(),
        )
    )
    if table_ids:
        stmt = stmt.where(Table.id.in_(list(table_ids)))
    else:
        stmt = stmt.where(Table.id.not_in(recently_maintained))

    candidates: list[Candidate] = []
    for row in session.execute(stmt).all():
        if not schema_in_maintenance_allow_list(row.schema_name, row.maintenance_schema_names):
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
    _remember_loaded(
        session,
        now=now,
        cooldown_hours=cooldown_hours,
        max_stats_age_days=max_stats_age_days,
        table_ids=table_ids,
        candidate_ids=[item.table_id for item in candidates],
    )
    return candidates


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _remember_loaded(
    session: Session,
    *,
    now: datetime,
    cooldown_hours: float,
    max_stats_age_days: float,
    table_ids: Sequence[uuid.UUID] | None,
    candidate_ids: Sequence[uuid.UUID],
) -> None:
    try:
        with session.begin_nested():
            exclusions = _exclusion_rows(
                session,
                now=now,
                cooldown_hours=cooldown_hours,
                max_stats_age_days=max_stats_age_days,
                table_ids=table_ids,
                candidate_ids=set(candidate_ids),
            )
        remember_loaded(
            engine_id=engine_id_for(session.get_bind()),
            evaluated_at=now,
            candidate_ids=candidate_ids,
            exclusions=exclusions,
        )
    except Exception:
        logger.exception("skip history exclusion note failed")
        clear_pending()


def _remember_ranked(
    candidates: Sequence[Candidate],
    ranked: Sequence[RankedCandidate],
    *,
    min_delete_file_pct: float,
    require_eligible: bool,
) -> None:
    try:
        ranked_ids = [item.candidate.table_id for item in ranked]
        ranked_set = set(ranked_ids)
        no_work: list[uuid.UUID] = []
        limit_cut: list[uuid.UUID] = []
        for candidate in candidates:
            if candidate.table_id in ranked_set:
                continue
            if require_eligible and not is_eligible(
                candidate, min_delete_file_pct=min_delete_file_pct
            ):
                no_work.append(candidate.table_id)
            else:
                limit_cut.append(candidate.table_id)
        remember_ranked(
            candidate_ids=[candidate.table_id for candidate in candidates],
            ranked_ids=ranked_ids,
            no_work_ids=no_work,
            limit_cut_ids=limit_cut,
        )
    except Exception:
        logger.exception("skip history rank note failed")
        clear_pending()


def _remember_budget(
    ranked: Sequence[RankedCandidate],
    kept: Sequence[RankedCandidate],
    over: Sequence[RankedCandidate],
) -> None:
    try:
        remember_budget(
            ranked_ids=[item.candidate.table_id for item in ranked],
            kept_ids=[item.candidate.table_id for item in kept],
            over_ids=[item.candidate.table_id for item in over],
        )
    except Exception:
        logger.exception("skip history budget note failed")
        clear_pending()


def _exclusion_rows(
    session: Session,
    *,
    now: datetime,
    cooldown_hours: float,
    max_stats_age_days: float,
    table_ids: Sequence[uuid.UUID] | None,
    candidate_ids: set[uuid.UUID],
) -> list[tuple[uuid.UUID, str, bool]]:
    stmt = (
        select(
            Table.id,
            Table.is_active,
            Table.schema_name,
            Table.last_scan_error_kind,
            Table.maintenance_retry_after,
            Source.enabled,
            Source.maintenance_schema_names,
            TableStats.scan_at,
            TableStats.needs_compaction,
            TableStats.delete_file_pct,
        )
        .select_from(Table)
        .join(Source, Source.id == Table.source_id)
        .outerjoin(TableStats, TableStats.table_id == Table.id)
    )
    if table_ids:
        stmt = stmt.where(Table.id.in_(list(table_ids)))
    cooled: set[uuid.UUID] = set()
    if not table_ids:
        cooled = set(
            session.scalars(
                select(MaintenanceJob.table_id)
                .where(
                    MaintenanceJob.status.in_(COOLDOWN_JOB_STATUSES),
                    MaintenanceJob.finished_at >= now - timedelta(hours=cooldown_hours),
                )
                .distinct()
            ).all()
        )
    rows: list[tuple[uuid.UUID, str, bool]] = []
    cutoff = _as_utc(now) - timedelta(days=max_stats_age_days)
    for row in session.execute(stmt):
        if row.id in candidate_ids:
            continue
        reason = _exclusion_reason(
            is_active=bool(row.is_active),
            source_enabled=bool(row.enabled),
            schema_name=row.schema_name,
            maintenance_schema_names=row.maintenance_schema_names,
            last_scan_error_kind=row.last_scan_error_kind,
            maintenance_retry_after=row.maintenance_retry_after,
            scan_at=row.scan_at,
            cooled=row.id in cooled,
            now=now,
            cutoff=cutoff,
        )
        rows.append(
            (
                row.id,
                reason,
                _row_has_work(row.scan_at, row.needs_compaction, row.delete_file_pct),
            )
        )
    return rows


def _exclusion_reason(
    *,
    is_active: bool,
    source_enabled: bool,
    schema_name: str,
    maintenance_schema_names: Any,
    last_scan_error_kind: str | None,
    maintenance_retry_after: datetime | None,
    scan_at: datetime | None,
    cooled: bool,
    now: datetime,
    cutoff: datetime,
) -> str:
    if not is_active or not source_enabled or last_scan_error_kind is not None:
        return REASON_EXCLUDED
    if maintenance_retry_after is not None and _as_utc(maintenance_retry_after) > _as_utc(now):
        return REASON_BACKOFF
    if scan_at is None or _as_utc(scan_at) < cutoff:
        return REASON_STALE_STATS
    if cooled:
        return REASON_COOLDOWN
    if not schema_in_maintenance_allow_list(schema_name, _allow_list(maintenance_schema_names)):
        return REASON_EXCLUDED
    return REASON_EXCLUDED


def _row_has_work(scan_at: datetime | None, needs_compaction: Any, delete_file_pct: Any) -> bool:
    if scan_at is None:
        return False
    return has_compaction_or_delete_work(
        needs_compaction=bool(needs_compaction),
        delete_file_pct=float(delete_file_pct or 0.0),
        min_delete_file_pct=DEFAULT_MIN_DELETE_FILE_PCT,
    )


def _allow_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item) for item in value]
    return None


class MaintenanceBlock(StrEnum):
    INACTIVE = "inactive"
    SOURCE_DISABLED = "source_disabled"
    STATS_STALE = "stats_stale"
    COOLDOWN = "cooldown"
    SCAN_RETRY_BACKOFF = "scan_retry_backoff"
    NO_SMALL_FILE_OR_DELETE_WORK = "no_small_file_or_delete_work"
    LAST_SCAN_ERROR = "last_scan_error"
    MAINTENANCE_RETRY_BACKOFF = "maintenance_retry_backoff"
    SCHEMA_NOT_ALLOWED = "schema_not_allowed"
    OPERATOR_EXCLUDED = "operator_excluded"


class MaintenancePredicate(StrEnum):
    LOAD_CANDIDATES = "load_candidates"
    IS_ELIGIBLE = "is_eligible"
    SCAN_RETRY_DUE_AT = "scan_retry_due_at"


@dataclass(frozen=True)
class MaintenanceEligibilityFacts:
    is_active: bool
    source_enabled: bool
    source_present: bool
    last_scan_error_kind: str | None
    maintenance_retry_after: datetime | None
    stats_present: bool
    scan_at: datetime | None
    schema_name: str
    maintenance_schema_names: Sequence[str] | None
    needs_compaction: bool
    delete_file_pct: float
    last_successful_finished_at: datetime | None
    consecutive_scan_failures: int
    last_scan_error_at: datetime | None
    # Value from scan_retry_due_at. This helper does not reimplement the ladder.
    scan_retry_due_at: datetime | None
    operator_excluded: bool = False


@dataclass(frozen=True)
class BlockingReason:
    code: MaintenanceBlock
    summary: str
    predicate: MaintenancePredicate
    blocks_nightly_selection: bool
    until: datetime | None = None
    values: dict[str, Any] = field(default_factory=dict)


def maintenance_blocking_reasons(
    facts: MaintenanceEligibilityFacts,
    *,
    now: datetime,
    cooldown_hours: float,
    max_stats_age_days: float,
    min_delete_file_pct: float = DEFAULT_MIN_DELETE_FILE_PCT,
) -> list[BlockingReason]:
    """Per-table gates mirrored from nightly selection, plus scan-retry backoff.

    ``blocks_nightly_selection`` is true only for predicates ``load_candidates`` applies
    (and ``is_eligible``, which nightly ``rank_candidates`` applies when ``table_ids`` is
    omitted). ``scan_retry_due_at`` is reported but does not fail selection by itself.
    ``fit_budget`` is not evaluated here.
    """
    current = _as_utc(now)
    reasons: list[BlockingReason] = []

    if not facts.is_active:
        reasons.append(
            _reason(
                MaintenanceBlock.INACTIVE,
                "Table is inactive (is_active=false)",
                MaintenancePredicate.LOAD_CANDIDATES,
                True,
                values={"is_active": False},
            )
        )

    if not facts.source_present:
        reasons.append(
            _reason(
                MaintenanceBlock.SOURCE_DISABLED,
                "Source row is missing (selection inner-joins an enabled source)",
                MaintenancePredicate.LOAD_CANDIDATES,
                True,
                values={"source_present": False, "source_enabled": False},
            )
        )
    elif not facts.source_enabled:
        reasons.append(
            _reason(
                MaintenanceBlock.SOURCE_DISABLED,
                "Source is disabled (enabled=false)",
                MaintenancePredicate.LOAD_CANDIDATES,
                True,
                values={"source_present": True, "source_enabled": False},
            )
        )

    stats_cutoff = current - timedelta(days=max_stats_age_days)
    if not facts.stats_present or facts.scan_at is None:
        reasons.append(
            _reason(
                MaintenanceBlock.STATS_STALE,
                (f"No stored stats (selection requires a scan within {max_stats_age_days} days)"),
                MaintenancePredicate.LOAD_CANDIDATES,
                True,
                values={
                    "max_stats_age_days": max_stats_age_days,
                    "scan_at": None,
                    "cutoff": stats_cutoff.isoformat(),
                    "stats_present": False,
                },
            )
        )
    else:
        scanned = _as_utc(facts.scan_at)
        if scanned < stats_cutoff:
            reasons.append(
                _reason(
                    MaintenanceBlock.STATS_STALE,
                    (
                        f"Stats are older than {max_stats_age_days} days "
                        f"(scan_at={scanned.isoformat()}, cutoff={stats_cutoff.isoformat()})"
                    ),
                    MaintenancePredicate.LOAD_CANDIDATES,
                    True,
                    values={
                        "max_stats_age_days": max_stats_age_days,
                        "scan_at": scanned.isoformat(),
                        "cutoff": stats_cutoff.isoformat(),
                        "stats_present": True,
                    },
                )
            )

    finished = facts.last_successful_finished_at
    if finished is not None:
        finished_at = _as_utc(finished)
        cooldown_cutoff = current - timedelta(hours=cooldown_hours)
        if finished_at >= cooldown_cutoff:
            until = finished_at + timedelta(hours=cooldown_hours)
            reasons.append(
                _reason(
                    MaintenanceBlock.COOLDOWN,
                    (
                        f"Cooldown until {until.isoformat()} "
                        f"(last succeeded or partial finish {finished_at.isoformat()}, "
                        f"cooldown_hours={cooldown_hours})"
                    ),
                    MaintenancePredicate.LOAD_CANDIDATES,
                    True,
                    until=until,
                    values={
                        "cooldown_hours": cooldown_hours,
                        "finished_at": finished_at.isoformat(),
                        "until": until.isoformat(),
                        "job_statuses": list(COOLDOWN_JOB_STATUSES),
                        "scope": "nightly",
                    },
                )
            )

    due = facts.scan_retry_due_at
    if due is not None and current < _as_utc(due):
        until = _as_utc(due)
        last_error_at = (
            _as_utc(facts.last_scan_error_at).isoformat() if facts.last_scan_error_at else None
        )
        reasons.append(
            _reason(
                MaintenanceBlock.SCAN_RETRY_BACKOFF,
                (
                    f"Scan retry backoff until {until.isoformat()} "
                    f"(consecutive_scan_failures={facts.consecutive_scan_failures}, "
                    f"last_scan_error_at={last_error_at})"
                ),
                MaintenancePredicate.SCAN_RETRY_DUE_AT,
                False,
                until=until,
                values={
                    "until": until.isoformat(),
                    "consecutive_scan_failures": facts.consecutive_scan_failures,
                    "last_scan_error_at": last_error_at,
                },
            )
        )

    if (
        facts.stats_present
        and facts.scan_at is not None
        and not has_compaction_or_delete_work(
            needs_compaction=facts.needs_compaction,
            delete_file_pct=facts.delete_file_pct,
            min_delete_file_pct=min_delete_file_pct,
        )
    ):
        reasons.append(
            _reason(
                MaintenanceBlock.NO_SMALL_FILE_OR_DELETE_WORK,
                (
                    "No small-file or delete work "
                    f"(needs_compaction={str(facts.needs_compaction).lower()}, "
                    f"delete_file_pct={facts.delete_file_pct}, "
                    f"min_delete_file_pct={min_delete_file_pct})"
                ),
                MaintenancePredicate.IS_ELIGIBLE,
                True,
                values={
                    "needs_compaction": facts.needs_compaction,
                    "delete_file_pct": facts.delete_file_pct,
                    "min_delete_file_pct": min_delete_file_pct,
                },
            )
        )

    if facts.last_scan_error_kind is not None:
        reasons.append(
            _reason(
                MaintenanceBlock.LAST_SCAN_ERROR,
                (
                    "Last scan could not read the table "
                    f"(last_scan_error_kind={facts.last_scan_error_kind})"
                ),
                MaintenancePredicate.LOAD_CANDIDATES,
                True,
                values={"last_scan_error_kind": facts.last_scan_error_kind},
            )
        )

    retry_after = facts.maintenance_retry_after
    if retry_after is not None and _as_utc(retry_after) > current:
        until = _as_utc(retry_after)
        reasons.append(
            _reason(
                MaintenanceBlock.MAINTENANCE_RETRY_BACKOFF,
                (
                    f"Maintenance retry backoff until {until.isoformat()} "
                    f"(maintenance_retry_after={until.isoformat()})"
                ),
                MaintenancePredicate.LOAD_CANDIDATES,
                True,
                until=until,
                values={"maintenance_retry_after": until.isoformat()},
            )
        )

    if facts.source_present and not schema_in_maintenance_allow_list(
        facts.schema_name, facts.maintenance_schema_names
    ):
        allowed = list(facts.maintenance_schema_names or [])
        reasons.append(
            _reason(
                MaintenanceBlock.SCHEMA_NOT_ALLOWED,
                (
                    f"Schema {facts.schema_name} is not in the source maintenance "
                    f"allow-list ({allowed})"
                ),
                MaintenancePredicate.LOAD_CANDIDATES,
                True,
                values={
                    "schema_name": facts.schema_name,
                    "maintenance_schema_names": allowed,
                },
            )
        )

    if facts.operator_excluded:
        reasons.append(
            _reason(
                MaintenanceBlock.OPERATOR_EXCLUDED,
                "An operator excluded this table or its schema from maintenance",
                MaintenancePredicate.LOAD_CANDIDATES,
                True,
            )
        )

    return reasons


def nightly_selection_eligible(reasons: Sequence[BlockingReason]) -> bool:
    return not any(reason.blocks_nightly_selection for reason in reasons)


def _reason(
    code: MaintenanceBlock,
    summary: str,
    predicate: MaintenancePredicate,
    blocks_nightly_selection: bool,
    *,
    until: datetime | None = None,
    values: dict[str, Any] | None = None,
) -> BlockingReason:
    return BlockingReason(
        code=code,
        summary=summary,
        predicate=predicate,
        blocks_nightly_selection=blocks_nightly_selection,
        until=until,
        values={} if values is None else values,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
