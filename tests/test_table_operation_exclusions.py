"""Per-table maintenance operation exclusions: the planner drops them on every plan path,
the Config API reports per-operation status, and changes are guarded and audited. Also covers
the explanations returned for every raw table property."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select

from cryo.db.models import (
    AuditLog,
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
    TableOperationExclusion,
)
from cryo.db.repositories.job_store import SQLJobStore
from cryo.db.repositories.operation_exclusions import set_excluded_operations
from cryo.domain.enums import Mode
from cryo.planner.execution import build_execution_plan
from cryo.planner.guardrails import CORRECT_ORDER
from cryo.planner.planner import build_plan
from cryo.table_config.operations import OPERATIONS
from cryo.table_config.property_docs import describe_property
from tests.auth_helpers import OPERATOR_HEADERS, VIEWER_HEADERS
from tests.test_batch_maintenance_coordinator import _harness
from tests.test_batch_maintenance_coordinator import _seed as _seed_batch
from tests.test_presets_api_db import ApiDb
from tests.test_table_config import _live
from tests.test_table_config import _seed as _seed_config

_STATS: dict[str, Any] = {
    "catalog": "iceberg",
    "schema_name": "analytics",
    "table_name": "events",
    "small_files_count": 500,
    "total_files": 600,
    "total_size_gb": 10,
    "total_delete_files": 40,
    "delete_file_pct": 30.0,
}


def _types(plan) -> list[str]:
    return [op.type.value for op in plan.operations]


def test_operation_catalogue_follows_the_canonical_order() -> None:
    assert tuple(spec.operation for spec in OPERATIONS) == CORRECT_ORDER


def test_excluding_optimize_also_stops_the_stats_pass_that_depends_on_it() -> None:
    params = {"compute_statistics": True, "stats_compute_ndv": True}
    baseline = build_plan(_STATS, [], Mode.DEEP, params)
    assert "optimize" in _types(baseline) and "compute_statistics" in _types(baseline)

    plan = build_plan(_STATS, [], Mode.DEEP, params, excluded_operations={"optimize"})

    assert "optimize" not in _types(plan)
    assert "compute_statistics" not in _types(plan)
    assert "expire_snapshots" in _types(plan) and "remove_orphan_files" in _types(plan)
    notes = [n for n in plan.notes if n.startswith("Excluded for this table")]
    assert notes == ["Excluded for this table in its maintenance settings: optimize."]


def test_excluding_optimize_lets_convert_deletes_run_unless_it_is_excluded_too() -> None:
    only_optimize = build_plan(_STATS, [], Mode.STANDARD, {}, excluded_operations={"optimize"})
    assert "convert_deletes" in _types(only_optimize)

    both = build_plan(
        _STATS, [], Mode.STANDARD, {}, excluded_operations={"optimize", "convert_deletes"}
    )
    assert not {"optimize", "convert_deletes"} & set(_types(both))


def test_excluded_cleanup_operations_are_dropped_and_noted_once() -> None:
    plan = build_plan(
        _STATS,
        [],
        Mode.DEEP,
        {},
        excluded_operations={"expire_snapshots", "remove_orphan_files"},
    )
    assert not {"expire_snapshots", "remove_orphan_files"} & set(_types(plan))
    assert (
        "Excluded for this table in its maintenance settings: expire_snapshots, "
        "remove_orphan_files." in plan.notes
    )


def test_manifest_only_mode_honours_exclusions() -> None:
    plan = build_plan(
        _STATS, [], Mode.MANIFEST_ONLY, {}, excluded_operations={"optimize_manifests"}
    )
    assert plan.operations == ()


def test_execution_plan_passes_exclusions_to_the_planner() -> None:
    class _Dest:
        engine = "spark_emr"
        config = {"catalog": "iceberg"}

    plan = build_execution_plan(
        stats=_STATS,
        partitions=[],
        mode=Mode.DEEP,
        params={},
        destination=_Dest(),
        operations=None,
        excluded_operations=("expire_snapshots",),
    )
    assert "expire_snapshots" not in _types(plan)


def test_job_records_carry_the_tables_exclusions(session) -> None:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    destination = Destination(id=uuid4(), name=f"dest-{uuid4()}", engine="trino", config={})
    table = Table(id=uuid4(), source_id=source.id, schema_name="a", table_name="b", is_active=True)
    session.add_all([source, destination, table])
    session.flush()
    run = MaintenanceRun(id=uuid4(), kind="adhoc", trigger="api", status="queued", summary={})
    session.add(run)
    session.flush()
    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode="deep",
        status="queued",
    )
    session.add(job)
    set_excluded_operations(
        session,
        table.id,
        ["remove_orphan_files", "optimize"],
        reason="writer compacts",
        actor="ops",
    )
    session.commit()

    record = SQLJobStore(lambda: session).get_job(str(job.id))

    assert record.excluded_operations == ("optimize", "remove_orphan_files")


def test_nightly_batch_drops_excluded_operations(session_factory) -> None:
    ids = _seed_batch(session_factory, tables=2)
    with session_factory() as session:
        set_excluded_operations(session, ids[0], ["optimize"], reason="owner compacts", actor="ops")
        session.commit()
    h = _harness(session_factory)

    h.coordinator.run(trigger="batch-cron")

    actions: dict[str, set[str]] = {}
    for manifest in h.runners[0].manifests.values():
        for table in manifest["tables"]:
            actions[table["table_id"]] = {s["action"] for s in table["statements"]}
    assert "optimize" not in actions[str(ids[0])]
    assert "optimize" in actions[str(ids[1])]


def _operations(body: dict) -> dict[str, dict]:
    return {op["operation"]: op for op in body["operations"]["operations"]}


def test_config_reports_what_runs_for_each_operation(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed_config(session)
    session.close()
    with patch("cryo.table_config.live.read_live_config", return_value=_live()):
        response = api_db.client.get(
            f"/api/v1/tables/{table_id}/config?refresh=true", headers=VIEWER_HEADERS
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["operations"]["mode"] == "standard"
    ops = _operations(body)
    assert list(ops) == [op.value for op in CORRECT_ORDER]
    assert ops["expire_snapshots"]["status"] == "runs"
    assert ops["optimize"]["status"] == "when_needed"
    assert ops["remove_orphan_files"]["status"] == "not_in_mode"
    assert ops["compute_statistics"]["status"] == "off"
    assert ops["expire_snapshots"]["exclude_risk"] == "high"
    assert ops["optimize"]["companion_hint"]
    assert ops["optimize"]["companion_operation"] == "convert_deletes"


def test_gc_disabled_blocks_the_deleting_operations(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed_config(session)
    session.close()
    live = _live(properties={"gc.enabled": "false"})
    with patch("cryo.table_config.live.read_live_config", return_value=live):
        body = api_db.client.get(
            f"/api/v1/tables/{table_id}/config?refresh=true", headers=VIEWER_HEADERS
        ).json()
    ops = _operations(body)
    assert ops["expire_snapshots"]["status"] == "blocked"
    assert ops["remove_orphan_files"]["status"] == "blocked"
    assert ops["optimize"]["status"] == "when_needed"


def test_put_operations_excludes_audits_and_reenables(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed_config(session)
    session.close()
    url = f"/api/v1/tables/{table_id}/config/operations"
    with patch("cryo.table_config.live.read_live_config", return_value=_live()):
        excluded = api_db.client.put(
            url,
            headers=OPERATOR_HEADERS,
            json={"excluded": ["optimize", "convert_deletes"], "reason": "writer compacts"},
        )
        assert excluded.status_code == 200, excluded.text
        body = excluded.json()
        assert body["added"] == ["convert_deletes", "optimize"]
        assert len(body["audit_log_ids"]) == 1
        optimize = next(o for o in body["operations"]["operations"] if o["operation"] == "optimize")
        assert optimize["excluded"] is True and optimize["status"] == "excluded"
        assert optimize["excluded_by"] and optimize["excluded_reason"] == "writer compacts"

        reenabled = api_db.client.put(
            url,
            headers=OPERATOR_HEADERS,
            json={"excluded": ["convert_deletes"], "reason": "writer stopped compacting"},
        )
    assert reenabled.status_code == 200, reenabled.text
    assert reenabled.json()["removed"] == ["optimize"]
    session = api_db.session_factory()
    rows = session.scalars(
        select(TableOperationExclusion.operation).where(
            TableOperationExclusion.table_id == uuid.UUID(str(table_id))
        )
    ).all()
    actions = [
        a.action
        for a in session.scalars(
            select(AuditLog).where(AuditLog.table_id == table_id).order_by(AuditLog.id)
        )
    ]
    session.close()
    assert rows == ["convert_deletes"]
    assert actions == ["operation_exclude", "operation_include"]


def test_excluding_expire_needs_confirmation(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed_config(session)
    session.close()
    url = f"/api/v1/tables/{table_id}/config/operations"
    payload: dict[str, Any] = {"excluded": ["expire_snapshots"], "reason": "legal hold on data"}
    with patch("cryo.table_config.live.read_live_config", return_value=_live()):
        unconfirmed = api_db.client.put(url, headers=OPERATOR_HEADERS, json=payload)
        confirmed = api_db.client.put(
            url, headers=OPERATOR_HEADERS, json={**payload, "confirm": True}
        )
    assert unconfirmed.status_code == 409
    assert unconfirmed.json()["detail"]["operations"] == ["expire_snapshots"]
    assert confirmed.status_code == 200, confirmed.text


def test_put_operations_rejects_bad_input_and_viewers(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed_config(session)
    session.close()
    url = f"/api/v1/tables/{table_id}/config/operations"
    unknown = api_db.client.put(
        url, headers=OPERATOR_HEADERS, json={"excluded": ["vacuum"], "reason": "long enough"}
    )
    short = api_db.client.put(
        url, headers=OPERATOR_HEADERS, json={"excluded": ["optimize"], "reason": "no"}
    )
    viewer = api_db.client.put(
        url, headers=VIEWER_HEADERS, json={"excluded": ["optimize"], "reason": "long enough"}
    )
    assert unknown.status_code == 422
    assert short.status_code == 422
    assert viewer.status_code == 403


def test_every_property_gets_an_explanation(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed_config(session)
    session.close()
    live = _live(
        properties={
            "write.target-file-size-bytes": "134217728",
            "write.parquet.bloom-filter-enabled.column.user_id": "true",
            "write.metadata.metrics.column.payload": "none",
            "read.split.target-size": "134217728",
            "owner": "etl",
            "team.cost-center": "42",
        }
    )
    with patch("cryo.table_config.live.read_live_config", return_value=live):
        body = api_db.client.get(
            f"/api/v1/tables/{table_id}/config?refresh=true", headers=VIEWER_HEADERS
        ).json()
    details = {d["key"]: d for d in body["property_details"]}
    assert set(details) == set(body["properties"])
    assert all(d["summary"] for d in details.values())
    assert details["write.target-file-size-bytes"]["kind"] == "setting"
    assert details["write.target-file-size-bytes"]["setting_key"] == "write.target-file-size-bytes"
    bloom = details["write.parquet.bloom-filter-enabled.column.user_id"]
    assert bloom["kind"] == "write"
    assert bloom["setting_key"] == "write.parquet.bloom-filter-columns"
    assert details["write.metadata.metrics.column.payload"]["kind"] == "write"
    assert details["read.split.target-size"]["kind"] == "read"
    assert details["owner"]["kind"] == "engine"
    assert details["team.cost-center"]["kind"] == "custom"


def test_property_docs_only_link_to_the_iceberg_reference() -> None:
    for key in ("read.split.target-size", "write.wap.enabled", "commit.status-check.num-retries"):
        detail = describe_property(key, "x", {})
        assert detail.doc_url and detail.doc_url.startswith(
            "https://iceberg.apache.org/docs/latest/configuration/"
        )
    assert describe_property("whatever", "x", {}).doc_url is None


def test_position_delete_rewrite_is_flagged_unsupported_on_trino(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed_config(session, engine="trino")
    session.close()
    with patch("cryo.table_config.live.read_live_config", return_value=_live()):
        body = api_db.client.get(
            f"/api/v1/tables/{table_id}/config?refresh=true", headers=VIEWER_HEADERS
        ).json()
    ops = _operations(body)
    assert ops["rewrite_position_deletes"]["status"] == "unsupported"
    assert ops["convert_deletes"]["status"] == "when_needed"
