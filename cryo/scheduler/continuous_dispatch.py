"""Scheduler-side consumer of ``cryo:maintenance-eval`` for continuous presets (#77).

The commit watcher publishes one entry per observed commit batch. This dispatcher turns them
into at most one pending evaluation per table (a Redis sorted set scored by when the table
should next be looked at), evaluates due tables against their preset's continuous block, and
hands due tables to the scheduler's normal run path. The governor admits those jobs like any
other: continuous mode changes when a job is created, never how it is admitted.

Redis is a cache here. A periodic sweep of ``cryo.table_drift`` re-adds every drifted table,
so a Redis restart or a trimmed stream delays a trigger by one sweep and loses nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from cryo.observability.metrics import (
    continuous_coalesced_total,
    continuous_deferrals_total,
    continuous_events_consumed_total,
    continuous_pending_tables,
    continuous_triggers_total,
)
from cryo.planner.job_coverage import ActiveJob
from cryo.queue.redis_queue import is_missing_group_error
from cryo.scheduler.continuous import (
    AUDIT_COALESCED,
    AUDIT_DEFERRED,
    AUDIT_RESCAN,
    AUDIT_TRIGGER,
    ContinuousConfig,
    Decision,
    DriftView,
    History,
    Verdict,
    continuous_trigger,
    evaluate,
    parse_continuous,
)
from cryo.scheduler.rules import (
    PresetConfig,
    PresetRule,
    TableAssignment,
    TableCandidate,
    effective_preset,
)
from cryo.scheduler.scheduler import ScheduledRunDraft

logger = logging.getLogger(__name__)

MAINTENANCE_EVAL_STREAM = "cryo:maintenance-eval"
CONSUMER_GROUP = "cryo-scheduler-continuous"
CONSUMER_NAME = "scheduler"
SEEN_TTL_SECONDS = 86_400
RECHECK_SECONDS = 120
COALESCE_RECHECK_SECONDS = 300


class ContinuousTableStateLike(Protocol):
    @property
    def drift(self) -> DriftView: ...

    @property
    def history(self) -> History: ...

    @property
    def scan_at(self) -> datetime | None: ...


class ContinuousStore(Protocol):
    def drift_table_ids(self, *, limit: int) -> list[str]: ...

    def list_tables_by_ids(self, table_ids: Sequence[str]) -> list[TableCandidate]: ...

    def load_state(
        self,
        table_ids: Sequence[str],
        *,
        now: datetime,
        last_maintained: dict[str, datetime | None],
    ) -> Mapping[str, ContinuousTableStateLike]: ...

    def audit(
        self, table_id: str, action: str, *, status: str, detail: str, engine: str | None = None
    ) -> None: ...

    def request_rescan(self, table_id: str, *, redis_client: Any) -> bool: ...


@dataclass(frozen=True)
class ContinuousContext:
    presets: Sequence[PresetConfig]
    rules: Sequence[PresetRule]
    assignments: Sequence[TableAssignment]
    coverage: dict[str, list[ActiveJob]] | None


@dataclass(frozen=True)
class ContinuousTickResult:
    consumed: int = 0
    evaluated: int = 0
    triggered: int = 0
    jobs_enqueued: int = 0
    preset_ids: tuple[str, ...] = ()


BuildRun = Callable[[PresetConfig, TableCandidate, str, ContinuousContext], ScheduledRunDraft]
SubmitRun = Callable[
    [ScheduledRunDraft, PresetConfig, list[TableCandidate], ContinuousContext], int
]


class ContinuousDispatcher:
    def __init__(
        self,
        store: ContinuousStore,
        redis_client: Any,
        *,
        events_per_tick: int = 500,
        evaluations_per_tick: int = 50,
        sweep_seconds: float = 300.0,
        rescan_wait_seconds: int = 1800,
        namespace: str = "cryo",
    ) -> None:
        self._store = store
        self._redis = redis_client
        self._events_per_tick = events_per_tick
        self._evaluations_per_tick = evaluations_per_tick
        self._sweep_seconds = sweep_seconds
        self._rescan_wait = timedelta(seconds=rescan_wait_seconds)
        self._ns = namespace
        self._last_sweep: datetime | None = None

    # ---- intake ---------------------------------------------------------------------------

    def consume(self, now: datetime) -> int:
        """Move new stream entries into the pending set. Returns entries read."""
        read = 0
        # Own unacknowledged entries first (a crash between read and ack), then new ones.
        for start in ("0", ">"):
            for message_id, fields in self._read(start):
                read += 1
                self._intake(fields, now)
                self._redis.xack(MAINTENANCE_EVAL_STREAM, CONSUMER_GROUP, message_id)
        return read

    def _read(self, start: str) -> list[tuple[Any, Mapping[Any, Any]]]:
        def command() -> Any:
            return self._redis.xreadgroup(
                CONSUMER_GROUP,
                CONSUMER_NAME,
                {MAINTENANCE_EVAL_STREAM: start},
                count=self._events_per_tick,
            )

        self._ensure_group()
        try:
            response = command()
        except Exception as exc:
            if not is_missing_group_error(exc):
                raise
            logger.warning("Consumer group %s is gone; recreating it", CONSUMER_GROUP)
            self._ensure_group()
            response = command()
        entries: list[tuple[Any, Mapping[Any, Any]]] = []
        for _, rows in response or []:
            entries.extend((message_id, fields) for message_id, fields in rows if fields)
        return entries

    def _ensure_group(self) -> None:
        try:
            self._redis.xgroup_create(
                MAINTENANCE_EVAL_STREAM, CONSUMER_GROUP, id="0", mkstream=True
            )
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def _intake(self, fields: Mapping[Any, Any], now: datetime) -> None:
        table_id = _field(fields, "table_id")
        snapshot_id = _field(fields, "snapshot_id")
        if not table_id or not snapshot_id:
            continuous_events_consumed_total.labels(outcome="invalid").inc()
            return
        if int(_field(fields, "writer_commits") or "0") <= 0:
            # Maintenance's own commits: never drift.
            continuous_events_consumed_total.labels(outcome="maintenance").inc()
            return
        seen_key = f"{self._ns}:continuous:seen:{table_id}:{snapshot_id}"
        if not self._redis.set(seen_key, "1", nx=True, ex=SEEN_TTL_SECONDS):
            continuous_events_consumed_total.labels(outcome="duplicate").inc()
            return
        continuous_events_consumed_total.labels(outcome="new").inc()
        self._mark_pending(table_id, now, keep_existing=True)
        self._redis.hincrby(self._key("merged"), table_id, 1)

    def sweep(self, now: datetime) -> int:
        """Re-add drifted tables from the DB; the backstop for anything Redis lost."""
        if self._last_sweep is not None and (now - self._last_sweep).total_seconds() < (
            self._sweep_seconds
        ):
            return 0
        self._last_sweep = now
        table_ids = self._store.drift_table_ids(limit=self._evaluations_per_tick * 20)
        for table_id in table_ids:
            self._mark_pending(table_id, now, keep_existing=True)
        return len(table_ids)

    # ---- evaluation -----------------------------------------------------------------------

    def tick(
        self,
        now: datetime,
        *,
        load_context: Callable[[], ContinuousContext],
        build_run: BuildRun,
        submit: SubmitRun,
    ) -> ContinuousTickResult:
        current = now.astimezone(UTC)
        consumed = self.consume(current)
        self.sweep(current)
        due = self._due(current)
        self._report_pending()
        if not due:
            return ContinuousTickResult(consumed=consumed)

        tables = {table.id: table for table in self._store.list_tables_by_ids(due)}
        for table_id in due:
            if table_id not in tables:
                self._drop(table_id)
        if not tables:
            return ContinuousTickResult(consumed=consumed, evaluated=len(due))
        states = self._store.load_state(
            list(tables),
            now=current,
            last_maintained={tid: table.last_maintained_at for tid, table in tables.items()},
        )
        context = load_context()

        triggered = 0
        enqueued = 0
        fired: list[str] = []
        for table_id, table in tables.items():
            state = states.get(table_id)
            resolved = effective_preset(table, context.presets, context.rules, context.assignments)
            preset = resolved.preset if resolved is not None else None
            config = _continuous_config(preset)
            if preset is None or config is None or state is None:
                self._drop(table_id)
                continue
            decision = evaluate(config, state.drift, state.history, now=current)
            if decision.verdict is Verdict.IDLE:
                self._drop(table_id)
                continue
            if decision.verdict is Verdict.WAIT:
                self._reschedule(table_id, decision.retry_at or current)
                continue
            if decision.verdict is Verdict.DEFER:
                self._defer(table_id, decision, current)
                continue
            count = self._dispatch_due(
                table, preset, config, state, decision, context, current, build_run, submit
            )
            if count:
                triggered += 1
                enqueued += count
                fired.append(preset.id)
        self._report_pending()
        return ContinuousTickResult(
            consumed=consumed,
            evaluated=len(due),
            triggered=triggered,
            jobs_enqueued=enqueued,
            preset_ids=tuple(fired),
        )

    def _dispatch_due(
        self,
        table: TableCandidate,
        preset: PresetConfig,
        config: ContinuousConfig,
        state: ContinuousTableStateLike,
        decision: Decision,
        context: ContinuousContext,
        now: datetime,
        build_run: BuildRun,
        submit: SubmitRun,
    ) -> int:
        table_id = table.id
        active = (context.coverage or {}).get(table_id) or []
        if active:
            self._coalesce(table_id, active[0].job_id, decision, now)
            return 0

        stale_note = self._await_rescan(table_id, state, now)
        if stale_note is None:
            return 0

        assert decision.signal is not None
        run = build_run(preset, table, continuous_trigger(decision.signal), context)
        if not run.jobs:
            self._defer(
                table_id,
                Decision(
                    Verdict.DEFER,
                    "preset_excludes_table",
                    decision.signal,
                    decision.value,
                    decision.threshold,
                    now + timedelta(seconds=config.min_interval_seconds),
                ),
                now,
            )
            return 0
        count = submit(run, preset, [table], context)
        if count == 0:
            self._coalesce(table_id, "concurrent", decision, now)
            return 0
        merged = int(_decode(self._redis.hget(self._key("merged"), table_id)) or "0")
        detail = (
            f"{decision.detail}; run_id={run.id} preset={preset.name} "
            f"commits={state.drift.commits} merged_events={merged}{stale_note}"
        )
        self._store.audit(table_id, AUDIT_TRIGGER, status="OK", detail=detail)
        continuous_triggers_total.labels(signal=decision.signal).inc()
        self._drop(table_id)
        return count

    def _await_rescan(
        self, table_id: str, state: ContinuousTableStateLike, now: datetime
    ) -> str | None:
        """None while the table waits for a fresher scan; otherwise a note for the audit.

        The planner works from stored scan stats. Planning a drift job from a scan older than
        the drift would target the partitions the scan saw, not the ones the writer touched.
        """
        first_commit = state.drift.first_commit_at
        if self._rescan_wait.total_seconds() <= 0 or first_commit is None:
            return ""
        if state.scan_at is not None and state.scan_at >= first_commit:
            self._redis.hdel(self._key("rescan"), table_id)
            return ""
        requested_raw = _decode(self._redis.hget(self._key("rescan"), table_id))
        if not requested_raw:
            if self._store.request_rescan(table_id, redis_client=self._redis):
                self._redis.hset(self._key("rescan"), table_id, now.isoformat())
                self._store.audit(
                    table_id,
                    AUDIT_RESCAN,
                    status="REQUESTED",
                    detail=("scan stats predate the drift; requested a table scan before planning"),
                )
                self._reschedule(table_id, now + timedelta(seconds=RECHECK_SECONDS))
                return None
            return "; scan_stats=stale (rescan request failed)"
        requested = datetime.fromisoformat(requested_raw)
        if now - requested < self._rescan_wait:
            self._reschedule(table_id, now + timedelta(seconds=RECHECK_SECONDS))
            return None
        return "; scan_stats=stale (rescan did not finish in time)"

    def _coalesce(self, table_id: str, job_id: str, decision: Decision, now: datetime) -> None:
        continuous_coalesced_total.inc()
        previous = _decode(self._redis.hget(self._key("coalesced"), table_id))
        if previous != job_id:
            self._redis.hset(self._key("coalesced"), table_id, job_id)
            self._store.audit(
                table_id,
                AUDIT_COALESCED,
                status="SKIPPED",
                detail=f"{decision.detail}; active_job={job_id}",
            )
        self._reschedule(table_id, now + timedelta(seconds=COALESCE_RECHECK_SECONDS))

    def _defer(self, table_id: str, decision: Decision, now: datetime) -> None:
        continuous_deferrals_total.labels(reason=decision.reason).inc()
        previous = _decode(self._redis.hget(self._key("deferred"), table_id))
        if previous != decision.reason:
            self._redis.hset(self._key("deferred"), table_id, decision.reason)
            self._store.audit(table_id, AUDIT_DEFERRED, status="DEFERRED", detail=decision.detail)
        self._reschedule(table_id, decision.retry_at or now + timedelta(seconds=RECHECK_SECONDS))

    # ---- pending set ----------------------------------------------------------------------

    def _due(self, now: datetime) -> list[str]:
        rows = self._redis.zrangebyscore(
            self._key("pending"), "-inf", now.timestamp(), start=0, num=self._evaluations_per_tick
        )
        return [_decode(row) for row in rows or []]

    def _mark_pending(self, table_id: str, at: datetime, *, keep_existing: bool) -> None:
        self._redis.zadd(self._key("pending"), {table_id: at.timestamp()}, nx=keep_existing)

    def _reschedule(self, table_id: str, at: datetime) -> None:
        self._redis.zadd(self._key("pending"), {table_id: at.timestamp()})

    def _drop(self, table_id: str) -> None:
        self._redis.zrem(self._key("pending"), table_id)
        for name in ("merged", "deferred", "coalesced", "rescan"):
            self._redis.hdel(self._key(name), table_id)

    def _report_pending(self) -> None:
        try:
            continuous_pending_tables.set(int(self._redis.zcard(self._key("pending")) or 0))
        except Exception:  # noqa: BLE001 - a gauge must never break the tick
            pass

    def _key(self, name: str) -> str:
        return f"{self._ns}:continuous:{name}"


def _continuous_config(preset: PresetConfig | None) -> ContinuousConfig | None:
    if preset is None or preset.paused or not preset.enabled:
        return None
    config, errors = parse_continuous(preset.continuous)
    if errors:
        logger.warning("Preset %s has an invalid continuous block: %s", preset.name, errors)
        return None
    if config is None or not config.enabled:
        return None
    return config


def _field(fields: Mapping[Any, Any], name: str) -> str:
    return _decode(fields.get(name.encode()) or fields.get(name))


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)
