from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cryo.capabilities import EffectStrategy
from cryo.db.repositories.intents import ReconciliationBacklog, ReconciliationEffect
from cryo.domain.enums import JobStatus, OperationType
from cryo.domain.intents import EffectState
from cryo.engines.trino.query_status import EngineOpState
from cryo.worker.intent_reconciler import IntentReconciler


@pytest.mark.parametrize(
    ("engine_state", "effect_state"),
    [
        (EngineOpState.SUCCEEDED, EffectState.SUCCEEDED),
        (EngineOpState.FAILED, EffectState.FAILED),
    ],
)
def test_reconciler_resolves_and_requeues_proven_effects(
    monkeypatch: pytest.MonkeyPatch,
    engine_state: EngineOpState,
    effect_state: EffectState,
) -> None:
    effect = _effect()
    intents = _IntentStore([effect])
    jobs = _JobStore()
    queue = _Queue()
    monkeypatch.setattr(
        "cryo.worker.intent_reconciler.probe_engine_op_state",
        lambda *args, **kwargs: engine_state,
    )

    result = IntentReconciler(
        intents=intents,
        jobs=jobs,
        queue=queue,
    ).run_once(now=datetime.now(UTC))

    assert result.resumed == 1
    assert intents.completed == [(effect.receipt_id, effect_state, True)]
    assert jobs.statuses == [(effect.job_id, JobStatus.QUEUED)]
    assert queue.enqueued == [(effect.job_id, "primary-destination")]


def test_reconciler_defers_unproven_and_manual_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running = _effect(receipt_id="running")
    manual = _effect(
        receipt_id="manual",
        strategy=EffectStrategy.MANUAL_RECONCILIATION,
    )
    intents = _IntentStore([running, manual])
    monkeypatch.setattr(
        "cryo.worker.intent_reconciler.probe_engine_op_state",
        lambda *args, **kwargs: EngineOpState.RUNNING,
    )

    result = IntentReconciler(
        intents=intents,
        jobs=_JobStore(),
        queue=_Queue(),
    ).run_once(now=datetime.now(UTC))

    assert result.waiting == 1
    assert result.manual_review == 1
    assert {receipt_id for receipt_id, _next_probe in intents.deferred} == {
        "running",
        "manual",
    }
    assert intents.completed == []


def test_reconciler_never_auto_replays_manual_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effect = _effect(
        submission={"continuation": "manual_review"},
    )
    intents = _IntentStore([effect])
    jobs = _JobStore()
    queue = _Queue()
    monkeypatch.setattr(
        "cryo.worker.intent_reconciler.probe_engine_op_state",
        lambda *args, **kwargs: EngineOpState.SUCCEEDED,
    )

    result = IntentReconciler(
        intents=intents,
        jobs=jobs,
        queue=queue,
    ).run_once(now=datetime.now(UTC))

    assert result.manual_review == 1
    assert intents.completed == [(effect.receipt_id, EffectState.SUCCEEDED, False)]
    assert jobs.statuses == []
    assert queue.enqueued == []


def test_reconciler_does_not_resume_superseded_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effect = _effect()
    intents = _IntentStore([effect], current=False)
    queue = _Queue()
    monkeypatch.setattr(
        "cryo.worker.intent_reconciler.probe_engine_op_state",
        lambda *args, **kwargs: EngineOpState.SUCCEEDED,
    )

    result = IntentReconciler(
        intents=intents,
        jobs=_JobStore(),
        queue=queue,
    ).run_once(now=datetime.now(UTC))

    assert result.superseded == 1
    assert queue.enqueued == []


def _effect(
    *,
    receipt_id: str = "receipt-1",
    strategy: EffectStrategy = EffectStrategy.STABLE_REFERENCE_PROBE,
    submission: dict[str, object] | None = None,
) -> ReconciliationEffect:
    return ReconciliationEffect(
        receipt_id=receipt_id,
        intent_id="intent-1",
        attempt_id="attempt-1",
        job_id="job-1",
        table_id="table-1",
        destination_id="destination-1",
        table_ref="iceberg.analytics.events",
        step_index=0,
        operation=OperationType.OPTIMIZE,
        strategy=strategy,
        state=EffectState.UNKNOWN,
        external_reference="query-1",
        submission=submission,
        probe_count=0,
    )


class _IntentStore:
    def __init__(
        self,
        effects: list[ReconciliationEffect],
        *,
        current: bool = True,
    ) -> None:
        self.effects = effects
        self.current = current
        self.deferred: list[tuple[str, datetime]] = []
        self.completed: list[tuple[str, EffectState, bool]] = []

    def list_reconciliation_effects(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[ReconciliationEffect]:
        return self.effects[:limit]

    def reconciliation_backlog(self, *, now: datetime) -> ReconciliationBacklog:
        del now
        return ReconciliationBacklog(count=len(self.effects), oldest_created_at=None)

    def defer_effect_probe(
        self,
        receipt_id: str,
        *,
        next_probe_at: datetime,
    ) -> None:
        self.deferred.append((receipt_id, next_probe_at))

    def complete_reconciliation(
        self,
        receipt_id: str,
        *,
        state: EffectState,
        detail: str,
        make_ready: bool,
        now: datetime,
    ) -> bool:
        assert detail
        self.completed.append((receipt_id, state, make_ready))
        return self.current


class _JobStore:
    def __init__(self) -> None:
        self.statuses: list[tuple[str, JobStatus]] = []

    def get_destination(self, destination_id: str) -> object:
        return {"id": destination_id}

    def get_job(self, job_id: str) -> object:
        return type(
            "Job",
            (),
            {"id": job_id, "destination_id": "primary-destination"},
        )()

    def mark_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error: str | None = None,
    ) -> None:
        assert error is None
        self.statuses.append((job_id, status))


class _Queue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, str]] = []

    def enqueue(self, job_id: str, destination_id: str) -> None:
        self.enqueued.append((job_id, destination_id))
