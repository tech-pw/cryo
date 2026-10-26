from __future__ import annotations

import logging
import uuid
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any, Protocol, cast

from cryo.config import get_settings
from cryo.db.repositories.run_finalizer import finalize_open_runs
from cryo.db.session import SessionLocal
from cryo.domain.enums import EngineType, JobStatus
from cryo.domain.intents import IntentStatus
from cryo.domain.models import CapacitySnapshot
from cryo.governor.admission import (
    AdmissionConfig,
    ReplicaBudget,
    admission_config_from_destination,
    admission_job_from_record,
    compute_admission_budget,
    decide_job_admission,
    pack_fleet_budget,
)
from cryo.governor.native_admit import (
    _run_fallback_dispatches_concurrently,
    admit_native_k8s_jobs,
    destination_uses_k8s_dispatch,
    split_fallback_dispatch_wave,
)
from cryo.governor.prefetch import (
    batch_job_requires_approval,
    batch_pii_block_reason,
    prefetch_approved_operation_keys,
    prefetch_paused_destination_ids,
    prefetch_paused_table_ids,
    prefetch_table_stats,
)
from cryo.governor.roi_gate import GateAction, ROIGate
from cryo.observability.correlation import clear_context, set_context
from cryo.observability.metrics import (
    governor_admission_decisions,
    governor_qpp_decisions_total,
    governor_queue_depth,
    governor_wave_duration_seconds,
)
from cryo.queue.locks import LockStore
from cryo.queue.reconcile import (
    purge_stale_queue_entries,
    reconcile_deferred_jobs,
    reconcile_queued_jobs,
    reconcile_stale_running_jobs,
    reconcile_stuck_admitted_jobs,
)
from cryo.queue.redis_queue import MaintenanceQueue
from cryo.triggers.evaluator import TriggerEvaluator
from cryo.worker.worker import DestinationConfig, JobStore, MaintenanceJobRecord

logger = logging.getLogger(__name__)


class CapacityProvider(Protocol):
    def probe(self, destination: DestinationConfig) -> Mapping[str, CapacitySnapshot | int]: ...


class StaticCapacityProvider:
    def probe(self, destination: DestinationConfig) -> Mapping[str, CapacitySnapshot | int]:
        config = destination.config
        replicas = config.get("replicas") or config.get("coordinators") or ("default",)
        if isinstance(replicas, str):
            replicas = (replicas,)
        cap = int(config.get("per_replica_cap") or config.get("slot_cap") or 5)
        return {str(replica): CapacitySnapshot(capacity=cap, used=0) for replica in replicas}


@dataclass(frozen=True)
class WaveResult:
    admitted: int = 0
    deferred: int = 0
    blocked: int = 0
    orphaned: int = 0
    re_enqueued: int = 0
    reaped: int = 0


# Upper bound on pending entries read per destination per wave; the candidate window
# (slots * 4) is far smaller than this in any real deployment.
_MAX_WAVE_PENDING_READ = 2000

# How often the Redis in-flight counters are rebuilt from Postgres.
_IN_FLIGHT_REBUILD_INTERVAL_SECONDS = 300

_ADMITTED = "admitted"
_DEFERRED = "deferred"
_BLOCKED = "blocked"
_ORPHANED = "orphaned"


@dataclass(frozen=True)
class _ReconcileCounts:
    re_enqueued: int = 0
    orphaned: int = 0
    reaped: int = 0


@dataclass
class _WaveTally:
    """Wave totals and their per-destination breakdown, incremented together.

    Every outcome used to bump two counters by hand at each of the ~20 branches that produce
    one, which is an easy place to update one and forget the other -- the per-destination
    metric and the returned WaveResult could disagree about the same wave.
    """

    admitted: int = 0
    deferred: int = 0
    blocked: int = 0
    orphaned: int = 0
    per_destination: dict[str, dict[str, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int))
    )

    def record(self, destination_id: str, outcome: str, count: int = 1) -> None:
        if count <= 0:
            return
        setattr(self, outcome, getattr(self, outcome) + count)
        self.per_destination[destination_id][outcome] += count

    def emit_metrics(self) -> None:
        for destination_id, outcomes in self.per_destination.items():
            for outcome, count in outcomes.items():
                if count:
                    governor_admission_decisions.labels(
                        destination=destination_id, outcome=outcome
                    ).inc(count)


class _WorkerDrainHeadroom:
    """How many more jobs this wave may push onto the worker claim queue, shared across
    destinations so one destination cannot spend the whole fleet's headroom twice.

    None means the queue cannot report worker presence, in which case admission behaves as
    it did before this cap existed.
    """

    def __init__(self, remaining: int | None) -> None:
        self._remaining = remaining

    def exhausted(self) -> bool:
        return self._remaining is not None and self._remaining <= 0

    def clamp(self, replicas: list[str]) -> list[str]:
        if self._remaining is None:
            return replicas
        allowed = replicas[: self._remaining]
        self._remaining -= len(allowed)
        return allowed


@dataclass(frozen=True)
class _DestinationWave:
    """Everything the gates need for one destination's admission pass, resolved once."""

    destination: DestinationConfig
    destination_id: str
    config: AdmissionConfig
    live_capacity: Mapping[str, CapacitySnapshot | int]
    session: Any | None
    current: datetime
    approved_keys: dict[tuple[uuid.UUID, str], list[dict[str, Any]]]
    stats_cache: dict[uuid.UUID, Any]
    paused_table_ids: set[uuid.UUID]
    paused_effect_destination_ids: set[str]
    fleet_decisions: Mapping[str, Any]


def _record_deferred_admission(config: AdmissionConfig) -> None:
    from cryo.governor.bandit_feedback import record_deferred_admission

    record_deferred_admission(config)


def _register_admission(job_id: str, config: AdmissionConfig) -> None:
    from cryo.governor.bandit_feedback import register_admission

    register_admission(job_id, config)


class _PendingFallbackDispatch:
    """One native-unsupported SQL fallback still waiting for a later wave."""

    __slots__ = ("dispatch", "destination_config")

    def __init__(
        self,
        dispatch: Any,
        destination_config: Mapping[str, Any],
    ) -> None:
        self.dispatch = dispatch
        self.destination_config = destination_config


def _shadow_sampled(job_id: str, sample_rate: int) -> bool:
    """True for roughly 1-in-sample_rate job_ids, deterministically -- same job_id always
    samples the same way within a run, no per-wave counter to maintain across calls. See
    Settings.governor_shadow_sample_rate's own docstring for why this exists: the ROI-gate
    and resource-predictor shadow paths were evaluating every single candidate every wave,
    purely to log data neither currently enforces."""
    if sample_rate <= 1:
        return True
    return hash(job_id) % sample_rate == 0


def _tenant_round_robin(
    jobs: list[MaintenanceJobRecord],
) -> list[MaintenanceJobRecord]:
    groups: dict[str, deque[MaintenanceJobRecord]] = {}
    order: list[str] = []
    for job in jobs:
        tenant = job.tenant_id or "default"
        if tenant not in groups:
            groups[tenant] = deque()
            order.append(tenant)
        groups[tenant].append(job)
    fair: list[MaintenanceJobRecord] = []
    while groups:
        for tenant in tuple(order):
            group = groups.get(tenant)
            if group is None:
                continue
            fair.append(group.popleft())
            if not group:
                del groups[tenant]
                order.remove(tenant)
    return fair


class Governor:
    def __init__(
        self,
        queue: MaintenanceQueue,
        lock_store: LockStore,
        store: JobStore,
        capacity_provider: CapacityProvider | None = None,
        *,
        default_config: AdmissionConfig | None = None,
        lock_ttl_seconds: int = 6 * 60 * 60,
        trigger_evaluator: TriggerEvaluator | None = None,
        enable_signal_triggers: bool = True,
        session_factory: Any | None = SessionLocal,
    ) -> None:
        self._queue = queue
        self._lock_store = lock_store
        self._store = store
        self._capacity_provider = capacity_provider or StaticCapacityProvider()
        self._default_config = default_config or AdmissionConfig()
        self._lock_ttl_seconds = lock_ttl_seconds
        self._replica_offsets: dict[str, int] = defaultdict(int)
        # Injectable so unit tests can run fully in-memory (queue/store/lock_store are already
        # fake) without also standing up a real Postgres connection just for the stale-job
        # reaper's step-ledger lookup. None means "no step ledger available", which
        # reconcile_stale_running_jobs already treats the same as "nothing found" -- a real
        # DB-lookup failure is handled separately, defensively, inside that function itself.
        self._session_factory = session_factory
        self._trigger_evaluator = trigger_evaluator or TriggerEvaluator()
        self._enable_signal_triggers = enable_signal_triggers
        settings = get_settings()
        self._qpp_enabled = settings.qpp_enabled
        self._roi_gate_enabled = settings.roi_gate_enabled
        self._roi_gate_shadow_mode = settings.roi_gate_shadow_mode
        self._roi_gate = ROIGate()
        self._shadow_sample_rate = settings.governor_shadow_sample_rate
        # Fallbacks not yet dispatched because the current wave already spent its
        # one slot_cap-sized concurrent batch. Drained at the end of
        # subsequent run_wave ticks so regular queue admission is never blocked
        # behind a multi-batch cohort.
        self._pending_fallback_dispatches: list[_PendingFallbackDispatch] = []
        self._last_in_flight_rebuild: float | None = None

    def _spark_engine_still_active(self, job: MaintenanceJobRecord) -> bool:
        """Real fix for a live incident, 2026-08-11: engine_activity_checker was declared as a
        parameter on reconcile_stale_running_jobs but never actually passed from anywhere in
        the codebase -- every RUNNING job past stale_after_seconds (default 6h) got reaped
        unconditionally, with no way to tell "worker died" from "job is huge and still going".
        Confirmed live: a 24TB rewrite legitimately still
        processing after ~5h, driver pod genuinely Running, would have been force-FAILED by
        this reaper within the hour despite doing real, uninterrupted work the whole time.

        Only implemented for spark_emr -- that's the engine large, long-running jobs actually
        route to (see cryo/engines/native/routing.py's SQL-fallback-required cases), and
        SparkK8sClient.get_job_run() already has the exact live signal this needs: the driver
        pod's own K8s phase, not the dispatch wrapper Job's, which is known unreliable
        (see get_job_run's own docstring). Any lookup failure or a destination this isn't
        built for returns False (not still active) -- matches reconcile_stale_running_jobs'
        existing conservative-but-not-silent-forever default elsewhere in this same function
        (the step-ledger check): when genuinely unsure, this reaper still reaps rather than
        leaving a truly-dead job stuck RUNNING forever, but a real live signal always wins.
        """
        if not job.engine_ref:
            return False
        destination = _try_get_destination(self._store, job.destination_id)
        if destination is None or destination.engine != EngineType.SPARK_EMR:
            return False
        try:
            from cryo.engines.spark.k8s import JobRunState
            from cryo.engines.spark.k8s_engine import SparkK8sEngine

            engine = SparkK8sEngine(config=dict(destination.config))
            result = engine.k8s_client.get_job_run(job.engine_ref)
            return result.state == JobRunState.RUNNING
        except Exception:
            logger.warning(
                "Spark engine-activity check failed for job %s (engine_ref=%s); "
                "treating as not-active",
                job.id,
                job.engine_ref,
                exc_info=True,
            )
            return False

    def _worker_drain_headroom(self) -> int | None:
        """How many more jobs this wave may push onto the worker claim queue, or None if the
        queue can't report worker presence (in which case admission behaves as it always did).

        Engine capacity and worker capacity are different things, and only the first was ever
        measured. LiveCapacityProvider probes Trino's running queries, Spark's live K8s Jobs,
        and the Redis in-flight counter -- none of which say anything about how fast workers
        pull from `cryo:admitted`. Workers process one job at a time, so the fleet drains at
        roughly (replicas / job duration), and anything admitted beyond that just queues.

        Live 2026-09-21: with the engine slot_cap satisfied, the governor admitted thousands
        of jobs per run against a fleet that can finish ~3,000 jobs/day; 5,952 of one run's
        6,117 jobs aged out of ADMITTED without an engine ever touching the table. Keeping a
        small multiple of the worker count queued is enough to keep every worker busy -- more
        than that buys no throughput and only creates work for the reaper.
        """
        active_workers = getattr(self._queue, "active_worker_count", None)
        admitted_depth = getattr(self._queue, "admitted_depth", None)
        if not callable(active_workers) or not callable(admitted_depth):
            return None
        workers = int(active_workers())
        if workers <= 0:
            # No worker has heartbeat recently. Either the fleet is genuinely down (admitting
            # is pointless) or heartbeats aren't being written yet on a mixed-version rollout.
            # Treating it as "unknown" rather than zero keeps the old behavior instead of
            # wedging admission fleet-wide on a signal that may simply be absent.
            return None
        allowance = workers * get_settings().worker_dispatch_backlog_per_replica
        return max(0, allowance - int(admitted_depth()))

    def run_wave(self, *, now: datetime | None = None) -> WaveResult:
        """One admission pass: repair queue state, then fill each destination's free slots.

        Everything that decides whether a single job may run lives in _gate_job, and the
        per-destination mechanics live in _admit_for_destination, so this stays a readable
        sequence of phases. It used to be one 620-line body nested ten levels deep, where the
        two things a reader actually needs to find -- the order the gates apply in, and which
        counter each branch bumps -- were impossible to see at once.
        """
        wave_start = perf_counter()
        tally = _WaveTally()
        try:
            current = now or datetime.now(UTC)
            reconciled = self._reconcile(current)
            tally.orphaned += reconciled.orphaned

            # Only the head of each queue: a wave can look at slots*4 candidates, so reading
            # (and decoding) a 30k-entry sorted set every 30s bought nothing.
            pending_by_destination = self._queue.pending_by_destination(
                limit=_MAX_WAVE_PENDING_READ
            )
            pending_depths: dict[str, int] = {}
            for destination_id in pending_by_destination:
                depth = self._queue.pending_depth(destination_id)
                pending_depths[destination_id] = depth
                governor_queue_depth.labels(destination=destination_id).set(depth)

            headroom = _WorkerDrainHeadroom(self._worker_drain_headroom())
            for destination_id, job_ids in pending_by_destination.items():
                self._admit_for_destination(
                    destination_id,
                    job_ids,
                    pending_depth=pending_depths[destination_id],
                    tally=tally,
                    headroom=headroom,
                    current=current,
                )

            self._run_one_fallback_dispatch_wave()

            return WaveResult(
                admitted=tally.admitted,
                deferred=tally.deferred,
                blocked=tally.blocked,
                orphaned=tally.orphaned,
                re_enqueued=reconciled.re_enqueued,
                reaped=reconciled.reaped,
            )
        finally:
            tally.emit_metrics()
            governor_wave_duration_seconds.observe(perf_counter() - wave_start)
            clear_context()

    def _reconcile(self, current: datetime) -> _ReconcileCounts:
        """Repair queue/DB disagreement before admitting anything new.

        Runs first every wave because each pass can free capacity this wave then spends:
        finalizing runs releases their jobs, and the reapers release in-flight slots and
        table locks held by jobs nothing will ever finish.
        """
        if self._session_factory is not None:
            finalize_open_runs(self._session_factory, now=current)
        re_enqueued = reconcile_queued_jobs(self._store, self._queue)
        re_enqueued += self._reconcile_deferred(current)
        orphaned = purge_stale_queue_entries(self._store, self._queue)
        reaped = reconcile_stale_running_jobs(
            self._store,
            self._queue,
            self._lock_store,
            now=current,
            stale_after_seconds=self._lock_ttl_seconds,
            step_ledger=self._step_ledger(),
            engine_activity_checker=self._spark_engine_still_active,
        )
        reaped += reconcile_stuck_admitted_jobs(
            self._store,
            self._queue,
            self._lock_store,
            now=current,
        )
        self._maybe_rebuild_in_flight()
        return _ReconcileCounts(re_enqueued=re_enqueued, orphaned=orphaned, reaped=reaped)

    def _maybe_rebuild_in_flight(self) -> None:
        """Re-derive each destination's in-flight counter from Postgres, on a timer.

        The Redis counter only moves at admit/complete/return_to_pending, so any path that
        ends an admitted job another way leaks a slot permanently and capacity drifts toward
        zero. On a timer rather than every wave: a worker finishing between the count and the
        reset skews the counter by one until the next rebuild, which is cheap to tolerate once
        in a while and not worth paying for on all waves.
        """
        count_in_flight = getattr(self._store, "in_flight_counts", None)
        reset = getattr(self._queue, "reset_in_flight", None)
        known = getattr(self._queue, "known_destinations", None)
        if not (callable(count_in_flight) and callable(reset) and callable(known)):
            return
        now = monotonic()
        if (
            self._last_in_flight_rebuild is not None
            and now - self._last_in_flight_rebuild < _IN_FLIGHT_REBUILD_INTERVAL_SECONDS
        ):
            return
        self._last_in_flight_rebuild = now
        try:
            counts = count_in_flight()
            for destination_id in set(counts) | set(known()):
                reset(destination_id, counts.get(destination_id, {}))
        except Exception:
            logger.warning("In-flight counter rebuild failed", exc_info=True)

    def _reconcile_deferred(self, current: datetime) -> int:
        """reconcile_deferred_jobs, told why each job was actually deferred.

        Without this it re-queues anything admission would take, including the jobs
        _gate_approval refused -- which the very next wave refuses again. See
        reconcile_deferred_jobs' own docstring for the measured loop.
        """
        # One session for the whole pass, with the lookups memoised per table: this runs
        # every wave, and the deferred backlog is bounded but not small. Without a session
        # factory (in-memory wiring) only the checks that need no database apply.
        session_context = (
            self._session_factory() if self._session_factory is not None else nullcontext(None)
        )
        with session_context as session:
            return reconcile_deferred_jobs(
                self._store,
                self._queue,
                now=current,
                blocked_reason=_still_blocked_reason(session, self._lock_store, now=current),
            )

    def _step_ledger(self) -> Any | None:
        if self._session_factory is None:
            return None
        try:
            from cryo.db.repositories.job_steps import JobStepStore

            return JobStepStore(self._session_factory)
        except Exception:
            logger.debug("JobStepStore unavailable for stale reaper", exc_info=True)
            return None

    def _admit_for_destination(
        self,
        destination_id: str,
        job_ids: list[str],
        *,
        pending_depth: int,
        tally: _WaveTally,
        headroom: _WorkerDrainHeadroom,
        current: datetime,
    ) -> None:
        destination = _try_get_destination(self._store, destination_id)
        if destination is None:
            failed = self._fail_orphaned_jobs(destination_id, job_ids)
            tally.record(destination_id, _ORPHANED, failed)
            return

        config = _admission_config(destination, self._default_config)
        live_capacity = self._capacity_provider.probe(destination)
        local_in_flight = self._queue.local_in_flight_by_replica(destination_id)
        budget = compute_admission_budget(live_capacity, local_in_flight, config)
        replicas = self._round_robin_slots(destination_id, budget.replicas)

        # Real incident, 2026-09-16: the native (iceberg-go) engine's trust boundary with Cryo
        # has no independent verification that a self-reported success actually mutated the
        # table -- see _dispatch_native_k8s, which this mirrors so worker backpressure is
        # applied to exactly the destinations that go through the worker claim queue.
        use_native_k8s = (
            destination_uses_k8s_dispatch(destination)
            and self._session_factory is not None
            and get_settings().native_engine_enabled
        )
        if not use_native_k8s:
            if headroom.exhausted():
                # The worker fleet already has as much queued as it can drain; adding more
                # buys no throughput. Distinct from having no engine capacity, which is
                # counted per candidate below.
                tally.record(destination_id, _BLOCKED, pending_depth)
                return
            replicas = headroom.clamp(replicas)

        session_factory = self._session_factory
        session_context = session_factory() if session_factory is not None else nullcontext(None)
        wave_start_replica_count = len(replicas)
        # Populated at the two direct-dispatch points below (worker-queue admit, and the
        # native-k8s-fallback-to-worker path) for the fleet-pack/dispatch-order counterfactual
        # telemetry. Not populated for admit_native_k8s_jobs' own aggregate path
        # (result.admitted is a count, not individual job_ids there) -- a partial signal.
        dispatched_job_ids: set[str] = set()
        native_k8s_buffer: list[MaintenanceJobRecord] = []

        with session_context as session:
            wave_jobs = self._collect_wave_candidates(
                destination_id,
                job_ids,
                slot_count=len(replicas),
                use_native_k8s=use_native_k8s,
                tally=tally,
            )
            wave = self._build_destination_wave(
                session,
                destination=destination,
                destination_id=destination_id,
                config=config,
                live_capacity=live_capacity,
                current=current,
                wave_jobs=wave_jobs,
            )

            for job in wave_jobs:
                if not replicas:
                    break
                set_context(
                    replace=True,
                    correlation_id=job.correlation_id or job.id,
                    run_id=job.run_id,
                    job_id=job.id,
                    intent_id=job.intent_id,
                    table_id=job.table_id,
                    destination_id=job.destination_id,
                )
                outcome = self._gate_job(job, wave)
                if outcome is not None:
                    tally.record(destination_id, outcome)
                    continue
                self._log_resource_predictor_shadow(job, wave)
                if use_native_k8s:
                    # Buffered for pack-on-admit; not pushed to the worker claim queue.
                    native_k8s_buffer.append(job)
                    continue
                self._admit_to_worker_queue(job, destination_id, config, replicas.pop(0))
                tally.record(destination_id, _ADMITTED)
                dispatched_job_ids.add(job.id)

            if native_k8s_buffer:
                self._dispatch_native_k8s(
                    native_k8s_buffer,
                    wave,
                    replicas=replicas,
                    tally=tally,
                    dispatched_job_ids=dispatched_job_ids,
                )

            self._log_fleet_pack_mismatch(
                wave,
                wave_jobs=wave_jobs,
                dispatched_job_ids=dispatched_job_ids,
                slot_count=wave_start_replica_count,
            )

    def _collect_wave_candidates(
        self,
        destination_id: str,
        job_ids: list[str],
        *,
        slot_count: int,
        use_native_k8s: bool,
        tally: _WaveTally,
    ) -> list[MaintenanceJobRecord]:
        """Load a bounded window of the pending queue, then interleave it by tenant.

        The cap preserves the 11k-fleet OOM fix while giving later tenants a chance at
        capacity instead of letting one queue prefix monopolize a wave. It also truncates
        before any priority ranking runs (pack_fleet_budget only ever sees this window), so a
        genuinely high-priority job past the cutoff is invisible this wave rather than merely
        deprioritized -- see docs/admission-ordering-and-scheduling.md.
        """
        candidate_limit = slot_count if use_native_k8s else max(slot_count, slot_count * 4)
        wave_jobs: list[MaintenanceJobRecord] = []
        for job_id in job_ids:
            if len(wave_jobs) >= candidate_limit:
                tally.record(destination_id, _BLOCKED)
                break
            try:
                wave_jobs.append(self._store.get_job(job_id))
            except KeyError:
                logger.warning("Purging queue entry for unknown job %s", job_id)
                self._queue.cancel_pending(job_id, destination_id)
                tally.record(destination_id, _ORPHANED)
        return _tenant_round_robin(wave_jobs)

    def _build_destination_wave(
        self,
        session: Any | None,
        *,
        destination: DestinationConfig,
        destination_id: str,
        config: AdmissionConfig,
        live_capacity: Mapping[str, CapacitySnapshot | int],
        current: datetime,
        wave_jobs: list[MaintenanceJobRecord],
    ) -> _DestinationWave:
        """Batch every per-table lookup the gates need into one round of queries.

        Done once per destination rather than per job: at ~800 candidates a wave, per-job
        lookups were the dominant cost of a wave.
        """
        table_uuids = {
            table_uuid for job in wave_jobs if (table_uuid := _job_table_uuid(job)) is not None
        }
        effect_destination_ids = {
            effect_destination_id
            for job in wave_jobs
            for effect_destination_id in job.intent_effect_destination_ids
        }
        if session is None:
            approved_keys: dict[tuple[uuid.UUID, str], list[dict[str, Any]]] = {}
            stats_cache: dict[uuid.UUID, Any] = {}
            paused_table_ids: set[uuid.UUID] = set()
            paused_effect_destination_ids: set[str] = set()
        else:
            approved_keys = prefetch_approved_operation_keys(session, table_uuids)
            stats_cache = prefetch_table_stats(session, table_uuids)
            paused_table_ids = prefetch_paused_table_ids(
                session, table_uuids, destination_id=destination_id
            )
            paused_effect_destination_ids = prefetch_paused_destination_ids(
                session, effect_destination_ids
            )
        return _DestinationWave(
            destination=destination,
            destination_id=destination_id,
            config=config,
            live_capacity=live_capacity,
            session=session,
            current=current,
            approved_keys=approved_keys,
            stats_cache=stats_cache,
            paused_table_ids=paused_table_ids,
            paused_effect_destination_ids=paused_effect_destination_ids,
            # AutoComp §A2 "Fleet Decide": computed once per destination over the whole wave,
            # not per job, so a big low-priority job doesn't block a wave's worth of
            # higher-MOOP-score jobs from any budget headroom it leaves behind.
            fleet_decisions=pack_fleet_budget(
                [_admission_job(job) for job in wave_jobs], config, now=current
            ),
        )

    def _gate_job(self, job: MaintenanceJobRecord, wave: _DestinationWave) -> str | None:
        """Decide whether one job may be dispatched this wave.

        Returns None to admit, or the outcome to record. Order matters and is the point of
        keeping these in one flat sequence: cheap local checks first, then the ones that need
        the database, then admission arithmetic, then the table lock -- which is the only gate
        with a side effect a later refusal has to undo.
        """
        for gate in (
            self._gate_paused,
            self._gate_abac,
            self._gate_intent,
            self._gate_approval,
            self._gate_pii,
            self._gate_admission,
            self._gate_roi,
        ):
            outcome = gate(job, wave)
            if outcome is not None:
                return outcome

        if not self._lock_store.acquire_table_lock(job.table_id, job.id, self._lock_ttl_seconds):
            self._defer(job, "table_locked")
            return _DEFERRED

        # Past this point a refusal must release the lock taken above.
        return self._gate_qpp(job, wave)

    def _gate_paused(self, job: MaintenanceJobRecord, wave: _DestinationWave) -> str | None:
        table_uuid = _job_table_uuid(job)
        paused = table_uuid in wave.paused_table_ids or any(
            effect_destination_id in wave.paused_effect_destination_ids
            for effect_destination_id in job.intent_effect_destination_ids
        )
        if not paused:
            return None
        reason = _PAUSED_REASON
        logger.info("Job %s deferred: %s", job.id, reason)
        self._defer(job, reason)
        return _DEFERRED

    def _gate_abac(self, job: MaintenanceJobRecord, wave: _DestinationWave) -> str | None:
        table_uuid = _job_table_uuid(job)
        if wave.session is None or table_uuid is None or not job.plan:
            return None
        from cryo.security.abac_enforcement import evaluate_maintenance_allowed

        for op in job.plan.get("operations") or []:
            op_type = op.get("type") if isinstance(op, dict) else op
            allowed, abac_reason = evaluate_maintenance_allowed(
                wave.session, table_uuid, None, str(op_type)
            )
            if not allowed:
                logger.warning("Deferring job %s: %s", job.id, abac_reason)
                _record_deferred_admission(wave.config)
                self._defer(job, str(abac_reason))
                return _DEFERRED
        return None

    def _gate_intent(self, job: MaintenanceJobRecord, wave: _DestinationWave) -> str | None:
        if job.intent_id is None:
            return None
        if job.intent_status != IntentStatus.READY.value or (
            job.intent_expires_at is None or _as_utc(job.intent_expires_at) <= wave.current
        ):
            reason = _intent_block_reason(job)
            logger.info("Job %s deferred: %s", job.id, reason)
            self._defer(job, reason)
            return _DEFERRED
        return None

    def _gate_approval(self, job: MaintenanceJobRecord, wave: _DestinationWave) -> str | None:
        table_uuid = _job_table_uuid(job)
        if wave.session is None or job.intent_id is not None or table_uuid is None:
            return None
        plan_operations = job.plan.get("operations") if job.plan else None
        if not batch_job_requires_approval(table_uuid, plan_operations, wave.approved_keys):
            return None
        logger.info("Job %s deferred: destructive operation requires approval", job.id)
        # Surface the block, not just log it -- see ensure_pending_approval_requests's own
        # docstring for the incident this fixes. Best-effort: a failure creating the request
        # must never block the defer itself.
        try:
            from cryo.workflows.approvals import ensure_pending_approval_requests

            ensure_pending_approval_requests(wave.session, table_uuid, plan_operations)
        except Exception as exc:
            logger.warning(
                "Failed to surface approval request for job %s table %s: %s",
                job.id,
                table_uuid,
                exc,
            )
        self._defer(job, _APPROVAL_REASON)
        return _DEFERRED

    def _gate_pii(self, job: MaintenanceJobRecord, wave: _DestinationWave) -> str | None:
        table_uuid = _job_table_uuid(job)
        if wave.session is None or table_uuid is None:
            return None
        plan_operations = job.plan.get("operations") if job.plan else None
        try:
            pii_reason = batch_pii_block_reason(
                wave.session,
                table_uuid,
                plan_operations,
                job_params=job.params,
                stats_cache=wave.stats_cache,
            )
        except Exception as exc:
            logger.warning("PII gate check failed for job %s table %s: %s", job.id, table_uuid, exc)
            self._defer(job, "pii_gate_check_failed")
            return _DEFERRED
        if pii_reason:
            logger.info("Job %s deferred: %s", job.id, pii_reason)
            self._defer(job, pii_reason)
            return _DEFERRED
        return None

    def _gate_admission(self, job: MaintenanceJobRecord, wave: _DestinationWave) -> str | None:
        """Cooldown, off-peak windows, deadlines and the per-destination budget.

        Skipped entirely for a job whose table tripped a signal trigger or that was forced by
        an operator -- both are explicit statements that this table needs work now.
        """
        if job.force_admit or self._signal_triggered(job, wave):
            return None

        fleet_decision = wave.fleet_decisions.get(job.id)
        if fleet_decision is not None and not fleet_decision.admissible:
            _record_deferred_admission(wave.config)
            self._defer(job, fleet_decision.reason)
            return _DEFERRED

        decision = decide_job_admission(_admission_job(job), wave.config, now=wave.current)
        if decision.admissible:
            return None
        if decision.defer:
            _record_deferred_admission(wave.config)
            self._defer(job, decision.reason)
            return _DEFERRED
        return _BLOCKED

    def _signal_triggered(self, job: MaintenanceJobRecord, wave: _DestinationWave) -> bool:
        table_uuid = _job_table_uuid(job)
        if wave.session is None or not self._enable_signal_triggers or table_uuid is None:
            return False
        try:
            trigger_result = self._trigger_evaluator.evaluate(
                wave.session, table_uuid, is_due_for_schedule=False
            )
        except Exception as exc:
            logger.warning(
                "Trigger evaluation failed for job %s table %s: %s", job.id, table_uuid, exc
            )
            return False
        return bool(trigger_result.should_run and trigger_result.trigger_type.value != "schedule")

    def _gate_roi(self, job: MaintenanceJobRecord, wave: _DestinationWave) -> str | None:
        """Benefit-cost ratio gate, currently running in shadow mode.

        In shadow mode decide() only ever logs, so sampling here skips redundant
        logging-only evaluations and never a real decision. Once enforcement is live
        (roi_gate_shadow_mode=False) every candidate is evaluated regardless of sample rate.
        """
        if not self._roi_gate_enabled or wave.session is None:
            return None
        if self._roi_gate_shadow_mode and not _shadow_sampled(job.id, self._shadow_sample_rate):
            return None
        roi = self._roi_gate.decide(wave.session, _admission_job(job))
        if roi is None:
            return None
        if roi.shadow:
            logger.info(
                "ROI gate shadow: job %s action=%s bcr=%.4f threshold=%.4f",
                job.id,
                roi.action.value,
                roi.bcr,
                roi.threshold,
            )
            return None
        if roi.action == GateAction.SKIP:
            self._queue.cancel_pending(job.id, wave.destination_id)
            self._store.mark_status(job.id, JobStatus.SKIPPED, error=roi.reason)
            return _BLOCKED
        if roi.action == GateAction.DEFER:
            self._defer(job, roi.reason)
            return _DEFERRED
        return None

    def _gate_qpp(self, job: MaintenanceJobRecord, wave: _DestinationWave) -> str | None:
        """Predicted-latency gate. Runs after the table lock, so a refusal releases it."""
        if not self._qpp_enabled:
            return None
        try:
            qpp_admit, predicted_p95_ms = _evaluate_qpp_for_governor(
                table_uuid=_job_table_uuid(job),
                stats_cache=wave.stats_cache,
                live_capacity=wave.live_capacity,
                job=job,
            )
        except Exception as exc:
            logger.warning("QPP gate failed for job %s: %s", job.id, exc)
            governor_qpp_decisions_total.labels(
                destination=wave.destination_id, outcome="error"
            ).inc()
            return None
        if not qpp_admit:
            logger.info("Job %s deferred: QPP refused (p95=%.0fms)", job.id, predicted_p95_ms)
            governor_qpp_decisions_total.labels(
                destination=wave.destination_id, outcome="refused"
            ).inc()
            self._defer(job, "qpp_refused")
            return _DEFERRED
        governor_qpp_decisions_total.labels(
            destination=wave.destination_id, outcome="confirmed"
        ).inc()
        return None

    def _log_resource_predictor_shadow(
        self, job: MaintenanceJobRecord, wave: _DestinationWave
    ) -> None:
        """Compare predicted sizing against the static cap. Logging only -- there is no
        enforcement path yet, which is why sampling here is unconditionally safe."""
        if not _shadow_sampled(job.id, self._shadow_sample_rate):
            return
        table_uuid = _job_table_uuid(job)
        if wave.session is None or table_uuid is None:
            return
        try:
            from cryo.ai.resource_predictor import predict_resources_for_table

            sizing = predict_resources_for_table(wave.session, table_uuid)
            logger.info(
                "Resource predictor (shadow) for table %s: cpu=%.1f mem=%.1fGiB p50=%.1fs "
                "p90=%.1fs conf=%.2f samples=%d static_cap=%d",
                table_uuid,
                sizing.recommended_cpu_cores,
                sizing.recommended_memory_gb,
                sizing.predicted_p50_seconds,
                sizing.predicted_p90_seconds,
                sizing.confidence,
                sizing.samples,
                wave.config.per_replica_cap,
            )
        except Exception as exc:
            logger.debug("Resource predictor shadow failed for %s: %s", table_uuid, exc)

    def _admit_to_worker_queue(
        self,
        job: MaintenanceJobRecord,
        destination_id: str,
        config: AdmissionConfig,
        replica: str,
    ) -> None:
        self._queue.admit(job.id, destination_id, replica)
        self._store.mark_status(job.id, JobStatus.ADMITTED, replica=replica)
        _register_admission(job.id, config)

    def _dispatch_native_k8s(
        self,
        buffered: list[MaintenanceJobRecord],
        wave: _DestinationWave,
        *,
        replicas: list[str],
        tally: _WaveTally,
        dispatched_job_ids: set[str],
    ) -> None:
        session_factory = self._session_factory
        assert session_factory is not None

        tables_by_job = {job.id: job.table_id for job in buffered}

        def _defer_native_job(job_id: str, reason: str) -> None:
            table_id = tables_by_job.get(job_id)
            if table_id is not None:
                self._lock_store.release_table_lock(table_id, job_id)
            self._queue.defer(job_id)
            self._store.mark_status(job_id, JobStatus.DEFERRED, error=reason)

        result = admit_native_k8s_jobs(
            jobs=buffered,
            destination=wave.destination,
            store=self._store,
            session_factory=session_factory,
            live_capacity=wave.live_capacity,
            stats_cache=wave.stats_cache,
            mark_status=self._store.mark_status,
            cancel_pending=self._queue.cancel_pending,
            defer_job=_defer_native_job,
            release_lock=self._lock_store.release_table_lock,
        )
        if result.fallback_to_worker:
            for job in buffered:
                if not replicas:
                    self._defer(job, "no_replica_slots")
                    tally.record(wave.destination_id, _DEFERRED)
                    continue
                self._admit_to_worker_queue(job, wave.destination_id, wave.config, replicas.pop(0))
                tally.record(wave.destination_id, _ADMITTED)
                dispatched_job_ids.add(job.id)
            return

        tally.record(wave.destination_id, _ADMITTED, result.admitted)
        tally.record(wave.destination_id, _DEFERRED, result.deferred)
        for job in buffered:
            _register_admission(job.id, wave.config)
        self._enqueue_fallback_dispatches(
            result.pending_fallback_dispatches, wave.destination.config
        )

    def _log_fleet_pack_mismatch(
        self,
        wave: _DestinationWave,
        *,
        wave_jobs: list[MaintenanceJobRecord],
        dispatched_job_ids: set[str],
        slot_count: int,
    ) -> None:
        """Read-only counterfactual: how much fleet rank and dispatch order disagree.

        Dispatch follows _tenant_round_robin's fairness interleaving, not pack_fleet_budget's
        moop_score rank, so a highly-ranked job can lose its slot to a lower-ranked one that
        came first. Computed after dispatch so it can never affect what was admitted; exists
        to measure how often this happens before deciding whether to change dispatch order --
        see docs/admission-ordering-and-scheduling.md, Phase S0.
        """
        if not wave.fleet_decisions or slot_count <= 0:
            return
        try:
            admissible = {job_id for job_id, dec in wave.fleet_decisions.items() if dec.admissible}

            def _rank_key(job: MaintenanceJobRecord) -> float:
                admission_job = _admission_job(job)
                if admission_job.moop_score is not None:
                    return admission_job.moop_score
                if admission_job.estimated_benefit_score is not None:
                    return admission_job.estimated_benefit_score
                return float("-inf")

            ranked = sorted(
                (job for job in wave_jobs if job.id in admissible),
                key=_rank_key,
                reverse=True,
            )
            missed = {job.id for job in ranked[:slot_count]} - dispatched_job_ids
            if missed:
                logger.info(
                    "Fleet-pack/dispatch-order mismatch for %s: %d of the top %d fleet-ranked "
                    "job(s) were not dispatched this wave (round-robin order took priority "
                    "over fleet rank)",
                    wave.destination_id,
                    len(missed),
                    slot_count,
                )
        except Exception as exc:  # noqa: BLE001 - telemetry only, must never affect dispatch
            logger.debug("Fleet-pack counterfactual telemetry failed: %s", exc)

    def _enqueue_fallback_dispatches(
        self,
        dispatches: tuple[Any, ...],
        destination_config: Mapping[str, Any],
    ) -> None:
        if not dispatches:
            return
        cfg = dict(destination_config)
        for dispatch in dispatches:
            self._pending_fallback_dispatches.append(_PendingFallbackDispatch(dispatch, cfg))

    def _run_one_fallback_dispatch_wave(self) -> None:
        """Dispatch at most one slot_cap concurrent batch, then yield.

        Regular async-queue admission already ran for every destination this tick.
        Remaining fallbacks stay on ``_pending_fallback_dispatches`` for later waves.
        """
        pending = self._pending_fallback_dispatches
        if not pending:
            return
        lead_cfg = pending[0].destination_config
        lead_key = lead_cfg.get("sql_fallback_destination_id")
        matching: list[Any] = []
        rest: list[_PendingFallbackDispatch] = []
        for item in pending:
            same = item.destination_config.get("sql_fallback_destination_id") == lead_key
            if same:
                matching.append(item.dispatch)
            else:
                rest.append(item)
        this_wave, later = split_fallback_dispatch_wave(
            matching,
            destination_config=lead_cfg,
            store=self._store,
        )
        self._pending_fallback_dispatches = [
            _PendingFallbackDispatch(dispatch, lead_cfg) for dispatch in later
        ] + rest
        if this_wave:
            _run_fallback_dispatches_concurrently(
                this_wave,
                destination_config=lead_cfg,
                store=self._store,
            )

    def _fail_orphaned_jobs(self, destination_id: str, job_ids: list[str]) -> int:
        logger.warning(
            "Purging %d queued job(s) for missing destination %s",
            len(job_ids),
            destination_id,
        )
        failed = 0
        for job_id in job_ids:
            self._queue.cancel_pending(job_id, destination_id)
            if not _is_valid_job_id(job_id):
                failed += 1
                continue
            try:
                self._store.mark_status(job_id, JobStatus.FAILED, error="destination_not_found")
                failed += 1
            except KeyError:
                logger.debug("Skipping orphan queue entry with no DB job record: %s", job_id)
        return failed

    def _round_robin_slots(
        self,
        destination_id: str,
        budgets: tuple[ReplicaBudget, ...],
    ) -> list[str]:
        expanded: list[str] = []
        replicas_with_slots: list[tuple[str, int]] = [
            (budget.replica, budget.available) for budget in budgets
        ]
        replicas_with_slots = [
            (replica, available) for replica, available in replicas_with_slots if available > 0
        ]
        if not replicas_with_slots:
            return []

        offset = self._replica_offsets[destination_id] % len(replicas_with_slots)
        ordered = replicas_with_slots[offset:] + replicas_with_slots[:offset]
        remaining = dict(ordered)
        index = 0
        while any(count > 0 for count in remaining.values()):
            replica = ordered[index % len(ordered)][0]
            if remaining[replica] > 0:
                expanded.append(replica)
                remaining[replica] -= 1
            index += 1

        self._replica_offsets[destination_id] = (offset + 1) % len(replicas_with_slots)
        return expanded

    def _defer(self, job: MaintenanceJobRecord, reason: str) -> None:
        # A deferred job must never keep its own table lock: nothing re-queues it while the
        # lock exists, and its siblings on the table defer as table_locked behind it until
        # the TTL runs out (#196). release_table_lock is compare-and-delete on the job id, so
        # this is a no-op for the gates that refuse before the lock is taken and for a lock
        # held by another job.
        self._lock_store.release_table_lock(job.table_id, job.id)
        self._queue.defer(job.id)
        self._store.mark_status(job.id, JobStatus.DEFERRED, error=reason)


_admission_job = admission_job_from_record

_PAUSED_REASON = "Maintenance execution is paused"
_APPROVAL_REASON = "Destructive operation requires approval"


def _try_get_destination(store: JobStore, destination_id: str) -> DestinationConfig | None:
    getter = getattr(store, "try_get_destination", None)
    if callable(getter):
        return cast(DestinationConfig | None, getter(destination_id))
    try:
        return store.get_destination(destination_id)
    except KeyError:
        return None


def _is_valid_job_id(job_id: str) -> bool:
    return bool(job_id) and job_id.lower() != "none"


def _still_blocked_predicate(
    session: Any,
    lock_store: LockStore,
    *,
    now: datetime,
) -> Callable[[MaintenanceJobRecord], bool]:
    """True while whatever deferred a job, other than admission arithmetic, still holds."""
    reason_for = _still_blocked_reason(session, lock_store, now=now)
    return lambda job: reason_for(job) is not None


def _still_blocked_reason(
    session: Any,
    lock_store: LockStore,
    *,
    now: datetime,
) -> Callable[[MaintenanceJobRecord], str | None]:
    """The reason a deferred job is still blocked, other than admission arithmetic, or None.

    Admission is only one of the things that defers a job; _gate_job's earlier gates and the
    table lock defer it too, and re-queueing a job the same gate refuses again next wave is
    pure churn that also occupies the head of the queue. This answers the other half of
    "would the gate chain take it now" for the gates that can be checked without side effects:
    the table lock, a pause, a durable intent that is not ready, and a pending approval.

    Anything this gets wrong in the True direction strands a job, so every lookup that raises
    is treated as "not blocked", restoring the old re-queue behaviour for that job rather
    than silently parking it.
    """
    approval_cache: dict[uuid.UUID, dict[tuple[uuid.UUID, str], list[dict[str, Any]]]] = {}
    paused_cache: dict[tuple[str, uuid.UUID], bool] = {}
    paused_destinations: dict[str, bool] = {}

    def intent_blocks(job: MaintenanceJobRecord) -> bool:
        if job.intent_id is None:
            return False
        if job.intent_status != IntentStatus.READY.value:
            return True
        return job.intent_expires_at is None or _as_utc(job.intent_expires_at) <= now

    def lock_blocks(job: MaintenanceJobRecord) -> bool:
        try:
            holder_of = getattr(lock_store, "table_lock_holder", None)
            if callable(holder_of):
                holder = holder_of(job.table_id)
                # The job's own lock (left behind by an older build that deferred without
                # releasing it) is not something to wait for: the job is about to retake it.
                return holder is not None and holder != job.id
            return bool(lock_store.is_table_locked(job.table_id))
        except Exception as exc:
            logger.warning("Lock re-check failed for deferred job %s: %s", job.id, exc)
            return False

    def pause_blocks(job: MaintenanceJobRecord, table_uuid: uuid.UUID | None) -> bool:
        try:
            if table_uuid is not None:
                key = (job.destination_id, table_uuid)
                if key not in paused_cache:
                    paused_cache[key] = table_uuid in prefetch_paused_table_ids(
                        session, {table_uuid}, destination_id=job.destination_id
                    )
                if paused_cache[key]:
                    return True
            effect_ids = {
                effect_id
                for effect_id in job.intent_effect_destination_ids
                if effect_id not in paused_destinations
            }
            if effect_ids:
                paused = prefetch_paused_destination_ids(session, effect_ids)
                for effect_id in effect_ids:
                    paused_destinations[effect_id] = effect_id in paused
            return any(paused_destinations.get(e) for e in job.intent_effect_destination_ids)
        except Exception as exc:
            logger.warning("Pause re-check failed for deferred job %s: %s", job.id, exc)
            return False

    def approval_blocks(job: MaintenanceJobRecord, table_uuid: uuid.UUID | None) -> bool:
        if job.intent_id is not None or table_uuid is None or not job.plan:
            return False
        operations = job.plan.get("operations")
        if not operations:
            return False
        try:
            approved = approval_cache.get(table_uuid)
            if approved is None:
                approved = prefetch_approved_operation_keys(session, {table_uuid})
                approval_cache[table_uuid] = approved
            return batch_job_requires_approval(table_uuid, operations, approved)
        except Exception as exc:
            logger.warning("Approval re-check failed for deferred job %s: %s", job.id, exc)
            return False

    def blocked_reason(job: MaintenanceJobRecord) -> str | None:
        if intent_blocks(job):
            return _intent_block_reason(job)
        if lock_blocks(job):
            return "table_locked"
        if session is None:
            return None
        table_uuid = _job_table_uuid(job)
        if pause_blocks(job, table_uuid):
            return _PAUSED_REASON
        if approval_blocks(job, table_uuid):
            return _APPROVAL_REASON
        return None

    return blocked_reason


def _intent_block_reason(job: MaintenanceJobRecord) -> str:
    if job.intent_status != IntentStatus.READY.value:
        return f"Durable intent is not ready (status={job.intent_status or 'missing'})"
    return "Durable intent expired before admission"


def _job_table_uuid(job: MaintenanceJobRecord) -> uuid.UUID | None:
    try:
        raw = job.table_id
        return uuid.UUID(raw) if isinstance(raw, str) else raw
    except (ValueError, AttributeError, TypeError):
        return None


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _admission_config(destination: DestinationConfig, default: AdmissionConfig) -> AdmissionConfig:
    """The admission config for one destination -- what the operator configured, unless bandit
    tuning is explicitly enabled (see Settings.governor_bandit_tuning_enabled)."""
    config = _admission_config_from_destination(destination, default)
    if not get_settings().governor_bandit_tuning_enabled:
        return config

    from cryo.db.session import SessionLocal
    from cryo.governor.bandit_store import load_bandit

    try:
        with SessionLocal() as session:
            bandit = load_bandit(session)
    except Exception as exc:
        logger.error(
            "Bandit state unavailable from Postgres; using static admission config: %s", exc
        )
        return config
    tuned, confident = bandit.suggest_config(config)
    return tuned if confident else config


def _bandit_path() -> Path:
    import os

    return Path(os.environ.get("CRYO_STATE_DIR", "/tmp/cryo")) / "admission_bandit.json"


_admission_config_from_destination = admission_config_from_destination


def _cotenant_load_pct(live_capacity: Mapping[str, object]) -> float:
    """Average load fraction across replicas, for the QPP gate."""
    if not live_capacity:
        return 0.0
    total = 0.0
    count = 0
    for _replica, snapshot in live_capacity.items():
        if isinstance(snapshot, CapacitySnapshot):
            used = max(snapshot.used, snapshot.queued)
            if snapshot.capacity > 0:
                total += used / snapshot.capacity
                count += 1
        elif isinstance(snapshot, (int, float)) and int(snapshot) > 0:
            total += int(snapshot) / 100
            count += 1
    return total / count if count else 0.0


def _evaluate_qpp_for_governor(
    *,
    table_uuid: uuid.UUID | None,
    stats_cache: Mapping[uuid.UUID, object],
    live_capacity: Mapping[str, object],
    job: MaintenanceJobRecord,
) -> tuple[bool, float]:
    """Run the variance-aware QPP gate for one governor admission decision.

    Returns ``(admit, predicted_p95_ms)``. ``admit=False`` means the job should be deferred.
    """
    from cryo.config import get_settings
    from cryo.governor.capacity import evaluate_qpp_for_job

    settings = get_settings()
    if not settings.qpp_enabled:
        return True, 0.0

    files_scanned = 0
    partition_count = 0
    if table_uuid is not None:
        stats = stats_cache.get(table_uuid)
        if stats is not None:
            files_scanned = getattr(stats, "total_files", 0) or 0
            partition_count = getattr(stats, "total_partitions", 0) or 0

    cotenant_load = _cotenant_load_pct(live_capacity)

    return evaluate_qpp_for_job(
        table_id=table_uuid,
        files_scanned=files_scanned,
        partition_count=partition_count,
        cotenant_load_pct=cotenant_load,
        target_p95_ms=settings.qpp_target_p95_ms,
        is_cached=False,
        query_text=None,
        model=None,
    )


def main() -> None:
    raise SystemExit("Governor core is library-only in this phase.")


if __name__ == "__main__":
    main()
