"""Regression coverage, 2026-08-03: NativeJobWatcher.tick() calling list_active_dispatches()
with no bound meant one read_namespaced_job K8s API call per active dispatch, every 30s tick,
regardless of how many were in flight. Real K8s Jobs were completing in 6-12s each, but with
hundreds of dispatches active at once (a fleet-wide native cohort trigger), the watcher fell
progressively further behind -- MaintenanceJob rows sat at status='running' for 20+ minutes
after their underlying K8s Job had already finished. Same root-cause shape as the admission-
scan bug fixed the same day (unbounded per-tick work scaling with backlog depth), just in the
completion path instead of admission.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from cryo.db.models import NativeDispatch
from cryo.db.repositories.native_dispatch import list_active_dispatches
from cryo.engines.native.watcher import NativeJobWatcher
from cryo.queue.locks import InMemoryLockStore


def _make_dispatch(*, created_at: datetime, status: str = "running") -> NativeDispatch:
    return NativeDispatch(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        destination_id=uuid.uuid4(),
        k8s_job_name=f"cryo-native-{uuid.uuid4().hex[:8]}",
        k8s_namespace="cryo",
        cpu_milli=500,
        mem_mib=1024,
        table_job_ids=[],
        status=status,
        created_at=created_at,
    )


def test_list_active_dispatches_respects_limit_and_orders_oldest_first(session) -> None:
    base = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    dispatches = [_make_dispatch(created_at=base + timedelta(seconds=i)) for i in range(10)]
    # Insert newest-first to prove ordering isn't accidentally matching insertion order.
    for dispatch in reversed(dispatches):
        session.add(dispatch)
    session.commit()

    unbounded = list_active_dispatches(session)
    assert len(unbounded) == 10
    assert [d.id for d in unbounded] == [d.id for d in dispatches]

    bounded = list_active_dispatches(session, limit=3)
    assert [d.id for d in bounded] == [d.id for d in dispatches[:3]]


def test_list_active_dispatches_excludes_terminal_statuses(session) -> None:
    base = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    active = _make_dispatch(created_at=base, status="running")
    done = _make_dispatch(created_at=base + timedelta(seconds=1), status="succeeded")
    session.add_all([active, done])
    session.commit()

    result = list_active_dispatches(session, limit=100)

    assert [d.id for d in result] == [active.id]


def test_watcher_tick_passes_configured_max_through_to_the_dispatch_scan() -> None:
    """A watcher configured with max_dispatches_per_tick=2 must call
    list_active_dispatches(limit=2) -- not the pre-fix unbounded call -- so a large active
    backlog can never make one tick's K8s API work scale with total backlog depth again."""
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)

    captured: dict[str, object] = {}

    def _fake_list_active_dispatches(sess, *, limit=None):
        captured["limit"] = limit
        return []  # empty active set -> tick() finishes immediately after this call

    batch_api = MagicMock()
    core_api = MagicMock()

    with (
        patch("cryo.engines.native.watcher._safe_load_k8s", return_value=(object(), object())),
        patch("kubernetes.config.load_incluster_config"),
        patch("kubernetes.client.BatchV1Api", return_value=batch_api),
        patch("kubernetes.client.CoreV1Api", return_value=core_api),
        patch(
            "cryo.engines.native.watcher.list_active_dispatches",
            side_effect=_fake_list_active_dispatches,
        ),
    ):
        watcher = NativeJobWatcher(
            session_factory=lambda: session,
            lock_store=InMemoryLockStore(),
            mark_status=lambda *a, **k: None,
            max_dispatches_per_tick=2,
        )
        watcher.tick()

    assert captured["limit"] == 2
