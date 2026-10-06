from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.domain.enums import JobStatus, Mode
from cryo.queue.redis_queue import MaintenanceQueue
from cryo.scheduler.rules import (
    PresetConfig,
    PresetRule,
    TableAssignment,
    TableCandidate,
    tables_for_preset,
)


@dataclass(frozen=True)
class ScheduledJobDraft:
    id: str
    run_id: str
    table_id: str
    destination_id: str
    mode: Mode
    status: JobStatus = JobStatus.QUEUED


@dataclass(frozen=True)
class ScheduledRunDraft:
    id: str
    preset_id: str
    trigger: str
    deadline_at: datetime | None
    jobs: tuple[ScheduledJobDraft, ...] = field(default_factory=tuple)


def build_scheduled_run(
    preset: PresetConfig,
    tables: Iterable[TableCandidate],
    presets: Iterable[PresetConfig],
    rules: Iterable[PresetRule],
    assignments: Iterable[TableAssignment] = (),
    *,
    trigger: str = "cron",
    budget_hint: int | None = None,
    now: datetime | None = None,
) -> ScheduledRunDraft:
    current = now or datetime.now(UTC)
    candidates = tables_for_preset(preset, tables, presets, rules, assignments, now=current)
    candidates = drop_recently_maintained(candidates, preset)
    candidates = apply_priority_order(candidates)
    if budget_hint is not None:
        candidates = candidates[:budget_hint]

    run_id = str(uuid4())
    deadline = _deadline_at(preset, current)
    jobs = tuple(
        ScheduledJobDraft(
            id=str(uuid4()),
            run_id=run_id,
            table_id=table.id,
            destination_id=preset.destination_id,
            mode=Mode(preset.mode),
        )
        for table in candidates
    )
    return ScheduledRunDraft(
        id=run_id,
        preset_id=preset.id,
        trigger=trigger,
        deadline_at=deadline,
        jobs=jobs,
    )


def fifo_priority(created_at: datetime | None = None) -> int:
    """Priority score giving older runs first admission at a shared destination.

    Live-verified 2026-09-01: every enqueue call site in the codebase left priority at its
    default (0), so with every job in a destination's Redis sorted set tied at the same
    score, ZREVRANGE (cryo/queue/redis_queue.py) falls back to reverse-lexicographic UUID
    ordering -- admission across different runs/presets sharing one destination was
    effectively random by job UUID, not fair or age-based. Confirmed live: two runs open
    28h with 621 combined jobs on spark-k8s-fallback got zero admissions for hours while a
    run created same-day kept getting slots, purely because its jobs' UUIDs happened to sort
    higher in the tie-broken order.

    Negated epoch seconds: an older run (smaller epoch) yields a larger (less negative)
    score, so ZREVRANGE's descending order puts it first -- the oldest run at a destination
    is always admitted ahead of anything newer, and no run can be starved indefinitely by an
    unlucky UUID once every other run ahead of it has drained.
    """
    at = created_at or datetime.now(UTC)
    return -int(at.timestamp())


def enqueue_run(
    run: ScheduledRunDraft,
    queue: MaintenanceQueue,
    *,
    priority: int | None = None,
    created_at: datetime | None = None,
) -> None:
    resolved_priority = priority if priority is not None else fifo_priority(created_at)
    for job in run.jobs:
        queue.enqueue(job.id, job.destination_id, resolved_priority)


def drop_recently_maintained(
    tables: Iterable[TableCandidate],
    preset: PresetConfig,
    *,
    now: datetime | None = None,
) -> list[TableCandidate]:
    cooldown_hours = int(preset.guardrails.get("cooldown_hours", 0) or 0)
    if cooldown_hours <= 0:
        return list(tables)

    current = now or datetime.now(UTC)
    kept: list[TableCandidate] = []
    for table in tables:
        last = table.last_maintained_at
        if not isinstance(last, datetime):
            kept.append(table)
            continue
        if _ensure_aware(last) + timedelta(hours=cooldown_hours) <= _ensure_aware(current):
            kept.append(table)
    return kept


def _query_weight(table: TableCandidate) -> float:
    """Trino-usage weight for ranking within a compaction_priority tier (PDAE, 2026-08-19).

    Confirmed live: cohort dispatch order previously had zero awareness of how often a table
    is actually queried -- ordering fell straight through to `small_files_count`, so two
    tables in the same priority tier, one queried 10,000x/month by real users and one queried
    zero times, had an equal shot at whichever slot came up first. That inverts the point of
    prioritizing at all: a fix landing sooner on a hot table has real, immediate query-latency
    and Trino-compute-cost impact; the same fix on a cold table has none until someone reads
    it, if ever.

    Blends two signals, both already present on TableStats and carried through in
    TableCandidate.stats with no query changes needed:
    - `query_count_30d`: real read volume, log-scaled so a 100,000-query table doesn't
      completely drown out a 500-query one that's still meaningfully "hot" -- a hard linear
      scale would make the tail (a handful of extremely hot tables) dominate every tier.
    - `projected_savings_usd_month`: this table's own S3/compute savings potential, so a
      table that's expensive to leave unmaintained (even at moderate query volume) doesn't
      get buried under a marginally-more-queried but cheap one.

    Returns higher = should sort earlier. Neither input existing (both 0/None) is the
    zero-query cold-table case this was missing -- correctly sorts last within its tier,
    same place it would have landed under the old small-file-only order.
    """
    query_count = float(table.stats.get("query_count_30d") or 0)
    savings_usd = float(table.stats.get("projected_savings_usd_month") or 0)
    return math.log1p(query_count) + math.log1p(max(savings_usd, 0.0))


def _reclaimable_weight(table: TableCandidate) -> float:
    """Reclaimable-bytes weight for ranking within a compaction_priority tier (2026-08-21).

    Added after a live investigation into safely draining a 57.95TB / 3,201-table
    expire_snapshots/remove_orphan_files backlog (built up during the ~2-week approval-gate
    outage, PDAE) without overwhelming the shared Hive Metastore. Confirmed live: the backlog
    is extremely concentrated by a power-law curve, not evenly spread -- the top 20 tables by
    reclaimable_bytes held 57% of the total 58TB, the top 200 held 97%. Sorting purely by
    query_weight (Trino usage) or small_files_count, as this ranking did before, has zero
    awareness of that concentration: two tables in the same priority tier, one holding 4.5TB
    of reclaimable garbage and one holding 4KB, had an equal shot at whichever slot came up
    first. Since every table dispatched costs roughly the same fixed metastore-connection
    overhead (a single connect+resolve-pointer round-trip per table, confirmed independent of
    how much data that table's cleanup actually touches -- see cryo.engines.native's
    job_dispatch.py timeout-fix comment for the connection-cost profile), draining
    highest-reclaimable-first reaches a given fraction of total backlog value in far fewer
    metastore round-trips than an unordered or usage-only sweep -- 200 well-chosen tables
    versus needing most of 3,201 to hit the same cumulative GB.

    log-scaled for the same reason query_weight is: a handful of multi-TB tables (the biggest
    held 4.5TB alone) would otherwise completely swamp every other ranking signal at linear
    scale, turning this into a single-factor sort in practice. GB (not raw bytes) keeps the
    log1p input in a numerically sane range matching _query_weight's own scale.
    """
    reclaimable_gb = float(table.stats.get("reclaimable_bytes") or 0) / (1024**3)
    return math.log1p(max(reclaimable_gb, 0.0))


def _combined_priority_weight(table: TableCandidate) -> float:
    """Blends reclaimable-bytes and query-hotness into one within-tier ranking signal
    (2026-08-25).

    Before this, apply_priority_order ranked strictly by reclaimable_weight first and only used
    query_weight to break exact ties -- so a table's query hotness had literally zero pull
    against reclaimable size unless two tables tied to several decimal places, which real data
    essentially never does. A hot, high-priority table sat behind many colder HIGH-tier tables
    purely because its reclaimable bytes were smaller, while its small-file count kept climbing
    because the fix never got a turn. A second heavily queried table with almost no reclaimable
    bytes was effectively unreachable under pure reclaimable-bytes ranking. Both were S3 GET-cost
    drivers from fragmentation Cryo's own scheduling was structurally unable to prioritize.
    Other HIGH-tier tables shared this shape -- not just these two.

    Sums the two signals (both already log1p-scaled to the same GB/count-derived magnitude, see
    each function's own docstring for why) rather than keeping them lexicographically separate,
    so a table that's extremely hot can now outrank one with a somewhat larger backlog, while a
    table holding a genuinely enormous backlog (the case _reclaimable_weight was built for)
    still wins over a merely-warm one -- neither signal can
    silently zero out the other's influence the way strict lexicographic ordering did.
    """
    return _reclaimable_weight(table) + _query_weight(table)


def apply_priority_order(tables: Iterable[TableCandidate]) -> list[TableCandidate]:
    priority_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return sorted(
        tables,
        key=lambda table: (
            priority_rank.get(str(table.stats.get("compaction_priority", "LOW")), 3),
            -_combined_priority_weight(table),
            -_reclaimable_weight(table),
            -_query_weight(table),
            -int(table.stats.get("small_files_count") or 0),
            table.schema_name,
            table.table_name,
        ),
    )


def _deadline_at(preset: PresetConfig, now: datetime) -> datetime | None:
    value = preset.guardrails.get("deadline_minutes")
    if value is None:
        return None
    return _ensure_aware(now) + timedelta(minutes=int(value))


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def main() -> None:
    raise SystemExit("Scheduler core is library-only in this phase.")


if __name__ == "__main__":
    main()
