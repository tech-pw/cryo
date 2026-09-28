"""Tests for ROIGate -- the admission gate that can PROCEED/SKIP/DEFER a job outright, not
just rank it. Covers the real-scan-cost wiring added to enhanced_bcr_from_table's
latency_benefit term, end-to-end through ROIGate.decide()."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.config import Settings
from cryo.db.models import QueryLog, Source, Table, TableCost, TableStats
from cryo.domain.enums import Mode
from cryo.governor.admission import AdmissionJob
from cryo.governor.roi_gate import ROIGate


def _seed_source(session) -> Source:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    return source


def _seed_table(session, *, schema_name: str, table_name: str) -> Table:
    source = _seed_source(session)
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name=schema_name,
        table_name=table_name,
        is_active=True,
    )
    session.add(table)
    session.flush()
    return table


def _seed_stats(session, table: Table, **overrides) -> TableStats:
    defaults = dict(
        table_id=table.id,
        scan_at=datetime.now(UTC),
        needs_compaction=True,
        compaction_priority="MED",
        grade="C",
        overall_score=50,
        total_files=500,
        small_files_count=500,
        total_size_gb=20.0,
        delete_file_pct=10.0,
        query_count_30d=100,
    )
    defaults.update(overrides)
    stats = TableStats(**defaults)
    session.add(stats)
    session.flush()
    return stats


def _seed_queries(session, table: Table, *, bytes_read: int, count: int = 10) -> None:
    source = session.get(Source, table.source_id)
    for i in range(count):
        session.add(
            QueryLog(
                source_id=source.id,
                table_id=table.id,
                query_id=f"{table.table_name}-query-{i}",
                engine="trino",
                query_text=f"SELECT * FROM {table.schema_name}.{table.table_name}",
                executed_at=datetime.now(UTC) - timedelta(hours=i),
                duration_ms=1000,
                bytes_read=bytes_read,
            )
        )


def _raw_with_roi_history(count: int) -> dict[str, object]:
    decision = {
        "ts": datetime.now(UTC).isoformat(),
        "action": "skip",
        "reason": "bcr_far_below_threshold",
        "bcr": 0.1,
        "threshold": 1.0,
        "shadow": True,
        "benefit_usd_month": 0.1,
        "cost_usd_month": 1.0,
    }
    return {"smart_maintenance": {"roi_decisions": [dict(decision) for _ in range(count)]}}


def _job_for(table: Table) -> AdmissionJob:
    return AdmissionJob(
        job_id=str(uuid4()),
        table_id=str(table.id),
        destination_id=str(uuid4()),
        mode=Mode.STANDARD,
    )


def test_roi_gate_uses_real_scan_cost_from_query_log(session, monkeypatch):
    """The actual admission-decision regression: two tables, identical fragmentation and
    identical query counts -- only the one whose queries scan real bytes should get a higher
    BCR. Shadow mode off so the effect is directly observable on `.action` too, not just the
    logged BCR."""
    monkeypatch.setenv("CRYO_ROI_GATE_SHADOW_MODE", "false")

    cheap_table = _seed_table(session, schema_name="test", table_name="roi_cheap")
    expensive_table = _seed_table(session, schema_name="test", table_name="roi_expensive")
    _seed_stats(session, cheap_table)
    _seed_stats(session, expensive_table)
    _seed_queries(session, cheap_table, bytes_read=10 * 1024 * 1024)  # 10MB/query
    _seed_queries(session, expensive_table, bytes_read=50 * 1024 * 1024 * 1024)  # 50GB/query
    session.commit()

    gate = ROIGate()
    cheap_decision = gate.decide(session, _job_for(cheap_table), record=False)
    expensive_decision = gate.decide(session, _job_for(expensive_table), record=False)

    assert expensive_decision.bcr > cheap_decision.bcr
    assert (
        expensive_decision.breakdown.latency_benefit_usd_month
        > cheap_decision.breakdown.latency_benefit_usd_month
    )


def test_roi_gate_unaffected_when_no_query_log_bytes(session, monkeypatch):
    """No QueryLog rows at all -- avg_bytes_per_query resolves to None inside ROIGate.decide(),
    and the decision must be identical to before this signal existed (regression guard against
    the wiring accidentally changing behavior for tables Cryo has no scan-cost data for yet)."""
    monkeypatch.setenv("CRYO_ROI_GATE_SHADOW_MODE", "false")

    table = _seed_table(session, schema_name="test", table_name="roi_no_query_log")
    _seed_stats(session, table)
    session.commit()

    gate = ROIGate()
    decision = gate.decide(session, _job_for(table), record=False)

    from cryo.planner.benefit_score import enhanced_bcr_from_table

    expected = enhanced_bcr_from_table(
        {
            "query_count_30d": 100,
            "small_files_count": 500,
            "delete_file_pct": 10.0,
            "total_size_gb": 20.0,
            "query_request_savings_usd_month": 0.0,
        }
    )
    # ROIGate.decide() rounds bcr to 6 places (roi_gate.py); the pure function's bcr is
    # unrounded, so compare with that same precision rather than exact equality.
    assert decision.bcr == round(expected.bcr, 6)


def test_roi_gate_scan_cost_can_move_a_table_from_defer_to_proceed(session, monkeypatch):
    """A concrete, real-world-shaped case: a table with modest fragmentation sits just below
    the PROCEED threshold on the old file-count-only proxy (DEFER), but its queries are
    genuinely expensive to scan -- with the real signal wired in, it crosses into PROCEED. This
    is the exact admission-gate consequence the file-count-only proxy could get wrong: a
    genuinely cost-worthwhile table getting deferred indefinitely because file count alone
    understated its benefit. Values below verified via enhanced_bcr_from_table directly before
    writing this test: 0.898 (DEFER, threshold 1.0) without the signal, 1.017 (PROCEED) with
    it -- same stats, same table, only avg_bytes_per_query differs."""
    monkeypatch.setenv("CRYO_ROI_GATE_SHADOW_MODE", "false")
    monkeypatch.setenv("CRYO_ROI_MIN_BCR", "1.0")

    table = _seed_table(session, schema_name="test", table_name="roi_borderline")
    _seed_stats(
        session,
        table,
        small_files_count=100,
        delete_file_pct=5.0,
        total_size_gb=2.0,
        query_count_30d=50,
    )
    session.add(
        TableCost(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            precision="exact",
            price_per_gb_month=0.023,
            live_bytes=1_000_000,
            dead_bytes=0,
            metadata_bytes=0,
            current_monthly_cost_usd=1.0,
            projected_monthly_cost_usd=0.95,
            projected_savings_usd_month=0.05,
        )
    )
    _seed_queries(session, table, bytes_read=100 * 1024 * 1024 * 1024, count=50)  # 100GB/query
    session.commit()

    gate = ROIGate()
    decision = gate.decide(session, _job_for(table), record=False)

    assert decision.bcr >= gate._settings.roi_min_bcr
    assert decision.action.value == "proceed"


def test_roi_gate_stays_in_shadow_after_enough_samples(session, monkeypatch):
    """A full decision history only flags readiness. With CRYO_ROI_GATE_SHADOW_MODE at its
    default (true), decide() must keep returning shadow decisions -- otherwise the governor
    starts enforcing SKIP/DEFER by itself once a table reaches roi_shadow_min_samples."""
    monkeypatch.delenv("CRYO_ROI_GATE_SHADOW_MODE", raising=False)
    # get_settings() is lru_cached before tests run; a fresh Settings() makes the delenv apply.
    monkeypatch.setattr("cryo.governor.roi_gate.get_settings", lambda: Settings())

    ready_table = _seed_table(session, schema_name="test", table_name="roi_shadow_ready")
    warming_table = _seed_table(session, schema_name="test", table_name="roi_shadow_warming")
    _seed_stats(session, ready_table, raw=_raw_with_roi_history(200))
    _seed_stats(session, warming_table, raw=_raw_with_roi_history(199))
    session.commit()

    gate = ROIGate()
    assert gate._settings.roi_gate_shadow_mode is True
    ready_decision = gate.decide(session, _job_for(ready_table), record=False)
    warming_decision = gate.decide(session, _job_for(warming_table), record=False)

    assert ready_decision.shadow is True
    assert ready_decision.ready_for_enforcement is True
    assert warming_decision.shadow is True
    assert warming_decision.ready_for_enforcement is False
