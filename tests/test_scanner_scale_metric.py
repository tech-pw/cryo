"""cli.py::_scanner_scale_metric -- the metric a scanner-worker KEDA ScaledObject would scale
on, mirroring _worker_scale_metric (see that function's own docstring for the July 2026 KEDA
blind-spot regression this pattern avoids: a queue-depth-only trigger drops to 0 the instant
work is claimed, long before it's actually finished).

Cost remediation plan §5, 2026-09-16 (docs/CRYO_UNUSED_COST_FIX_2026-09-16.md): the
scanner-worker HPA was already sitting at its floor of 3 replicas, mostly idle between ad-hoc
scan bursts. This test file covers the "pending + actively claimed" chunk count
(chunks_pending + chunks_running from MaintenanceRun.summary) that a real queue-depth-aware
scaler should use instead of raw Redis pending-list length.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import sessionmaker

from cryo.cli import _scanner_scale_metric
from cryo.db.models import MaintenanceRun


def _seed_scan_run(session, *, status: str, summary: dict) -> None:
    run = MaintenanceRun(id=uuid4(), kind="scan", trigger="cron", status=status, summary=summary)
    session.add(run)
    session.commit()


def test_sums_pending_and_running_chunks_across_active_scan_runs(session, monkeypatch) -> None:
    _seed_scan_run(
        session,
        status="running",
        summary={"chunks_pending": 2, "chunks_running": 1, "chunks_done": 0},
    )
    _seed_scan_run(
        session,
        status="queued",
        summary={"chunks_pending": 5, "chunks_running": 0, "chunks_done": 0},
    )

    monkeypatch.setattr(
        "cryo.db.session.SessionLocal", sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    )

    assert _scanner_scale_metric() == 8  # (2+1) + (5+0)


def test_finished_scan_runs_do_not_count(session, monkeypatch) -> None:
    """A scan run that already finished must not contribute -- its chunks_running/pending
    values are historical, not current demand, even if they were never zeroed out."""
    _seed_scan_run(
        session,
        status="succeeded",
        summary={"chunks_pending": 0, "chunks_running": 0, "chunks_done": 10},
    )
    _seed_scan_run(
        session,
        status="failed",
        summary={"chunks_pending": 3, "chunks_running": 2, "chunks_done": 0},
    )

    monkeypatch.setattr(
        "cryo.db.session.SessionLocal", sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    )

    assert _scanner_scale_metric() == 0


def test_non_scan_runs_do_not_count(session, monkeypatch) -> None:
    """A regular maintenance run's summary shape is unrelated -- must not accidentally be
    read as scan-chunk demand even if it happens to share key names."""
    _seed_scan_run(
        session,
        status="running",
        summary={"chunks_pending": 2, "chunks_running": 1},
    )
    other = MaintenanceRun(
        id=uuid4(),
        kind="scheduled",
        trigger="cron",
        status="running",
        summary={"chunks_pending": 99, "chunks_running": 99},
    )
    session.add(other)
    session.commit()

    monkeypatch.setattr(
        "cryo.db.session.SessionLocal", sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    )

    assert _scanner_scale_metric() == 3  # only the kind="scan" run's (2+1)


def test_zero_when_no_active_scan_runs(session, monkeypatch) -> None:
    monkeypatch.setattr(
        "cryo.db.session.SessionLocal", sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    )
    assert _scanner_scale_metric() == 0


def test_missing_or_non_dict_summary_does_not_crash(session, monkeypatch) -> None:
    _seed_scan_run(session, status="accepted", summary=None)

    monkeypatch.setattr(
        "cryo.db.session.SessionLocal", sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    )

    assert _scanner_scale_metric() == 0
