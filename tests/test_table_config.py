"""Table configuration inventory: catalogue integrity, value sources, recommendations,
direction-aware risk, the gc.enabled planner gate, and the config endpoints."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from cryo.db.models import (
    AuditLog,
    Destination,
    Preset,
    Source,
    Table,
    TableAssignment,
    TableStats,
)
from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.planner.execution import gate_gc_disabled, gc_disabled_in_stats
from cryo.table_config.context import ConfigContext, RefInfo, current_value
from cryo.table_config.live import CatalogReadError, LiveTableConfig, SpecField
from cryo.table_config.recommend import advise
from cryo.table_config.risk import assess, normalize
from cryo.table_config.settings import GROUPS, MIB, SETTINGS, SETTINGS_BY_KEY
from tests.auth_helpers import ADMIN_HEADERS, OPERATOR_HEADERS, VIEWER_HEADERS
from tests.test_presets_api_db import ApiDb

DAY_MS = 86_400_000


def _spec(key: str):
    return SETTINGS_BY_KEY[key]


def _ctx(**kwargs) -> ConfigContext:
    kwargs.setdefault("properties", {})
    return ConfigContext(**kwargs)


# ── Catalogue integrity ──────────────────────────────────────────────────────


def test_catalogue_keys_are_unique_and_grouped() -> None:
    keys = [spec.key for spec in SETTINGS]
    assert len(keys) == len(set(keys))
    group_ids = {group.id for group in GROUPS}
    assert all(spec.group in group_ids for spec in SETTINGS)
    assert all(any(spec.group == group for spec in SETTINGS) for group in group_ids)


def test_catalogue_uses_real_iceberg_mode_keys() -> None:
    """write.merge-mode et al. are not Iceberg keys; engines read write.merge.mode."""
    for spec in SETTINGS:
        assert not re.search(r"write\.(delete|update|merge)-mode", spec.key)
    assert {"write.delete.mode", "write.update.mode", "write.merge.mode"} <= set(SETTINGS_BY_KEY)


def test_catalogue_defaults_respect_their_own_editors() -> None:
    for spec in SETTINGS:
        if spec.options and spec.default is not None:
            assert spec.default in spec.options, spec.key
        if spec.default is not None and spec.value_type in {"bytes", "int", "duration_ms"}:
            value = int(spec.default)
            if spec.minimum is not None:
                assert value >= spec.minimum, spec.key
            if spec.maximum is not None:
                assert value <= spec.maximum, spec.key
        assert spec.doc_url.startswith("https://"), spec.key
        if not spec.editable:
            assert spec.read_only_reason, spec.key


def test_catalogue_defaults_match_iceberg() -> None:
    assert _spec("write.target-file-size-bytes").default == str(512 * MIB)
    assert _spec("write.delete.target-file-size-bytes").default == str(64 * MIB)
    assert _spec("commit.manifest.target-size-bytes").default == str(8 * MIB)
    assert _spec("history.expire.max-snapshot-age-ms").default == str(5 * DAY_MS)
    assert _spec("write.metadata.previous-versions-max").default == "100"
    assert _spec("write.metadata.metrics.default").default == "truncate(16)"
    assert _spec("write.delete.granularity").default == "partition"
    assert _spec("commit.retry.num-retries").default == "4"


# ── Value sources ────────────────────────────────────────────────────────────


def test_current_value_reports_where_the_value_comes_from() -> None:
    ctx = _ctx(
        properties={"write.target-file-size-bytes": "134217728"},
        format_version=2,
        cryo_preset_name="p",
        preset_params={"retention_days": 5},
        param_overrides={"retain_last": 3},
    )
    assert current_value(_spec("write.target-file-size-bytes"), ctx) == ("134217728", "table")
    assert current_value(_spec("write.delete.mode"), ctx) == ("copy-on-write", "iceberg_default")
    assert current_value(_spec("write.distribution-mode"), ctx) == (None, "engine_default")
    assert current_value(_spec("format-version"), ctx) == ("2", "metadata")
    assert current_value(_spec("cryo.retention_days"), ctx) == ("5", "cryo_preset")
    assert current_value(_spec("cryo.retain_last"), ctx) == ("3", "cryo_override")
    assert current_value(_spec("cryo.orphan_retention_days"), ctx) == ("5", "cryo_default")


def test_bloom_columns_collapse_per_column_properties() -> None:
    ctx = _ctx(
        properties={
            "write.parquet.bloom-filter-enabled.column.user_id": "true",
            "write.parquet.bloom-filter-enabled.column.ts": "false",
            "write.parquet.bloom-filter-enabled.column.order_id": "TRUE",
        }
    )
    assert current_value(_spec("write.parquet.bloom-filter-columns"), ctx) == (
        "order_id,user_id",
        "table",
    )


# ── Recommendations ──────────────────────────────────────────────────────────


def test_target_size_recommendation_follows_the_preset() -> None:
    ctx = _ctx(cryo_preset_name="hot", cryo_engine="spark_emr", preset_params={"file_size_mb": 384})
    advice = advise("write.target-file-size-bytes", ctx)
    assert advice.recommended == str(384 * MIB)
    assert "384 MiB" in (advice.reason or "")
    assert advice.note and "after every optimize" in advice.note


def test_target_size_has_no_recommendation_without_a_preset() -> None:
    assert advise("write.target-file-size-bytes", _ctx()).recommended is None


def test_distribution_and_codec_recommendations() -> None:
    partitioned = _ctx(partitioned=True)
    assert advise("write.distribution-mode", partitioned).recommended == "hash"
    assert advise("write.distribution-mode", _ctx(partitioned=False)).recommended is None
    assert advise("write.parquet.compression-codec", _ctx()).recommended == "zstd"
    configured = _ctx(properties={"write.parquet.compression-codec": "zstd"})
    assert advise("write.parquet.compression-codec", configured).recommended == "zstd"
    ranged = _ctx(partitioned=True, properties={"write.distribution-mode": "range"})
    assert advise("write.distribution-mode", ranged).recommended is None


def test_row_level_modes_get_a_note_not_a_value() -> None:
    advice = advise("write.merge.mode", _ctx(format_version=2, stats={"delete_file_pct": 12.0}))
    assert advice.recommended is None
    assert advice.note and "12.0%" in advice.note


def test_infinite_refs_are_called_out() -> None:
    ctx = _ctx(refs=(RefInfo("main", "branch", 1), RefInfo("release-1", "tag", 2)))
    advice = advise("history.expire.max-ref-age-ms", ctx)
    assert advice.note and "release-1" in advice.note


# ── Direction-aware risk ─────────────────────────────────────────────────────


def test_lowering_retention_is_high_and_raising_is_low() -> None:
    ctx = _ctx(cryo_preset_name="p", preset_params={"retention_days": 7}, cryo_engine="trino")
    spec = _spec("cryo.retention_days")
    lowered = assess(spec, "7", "3", ctx)
    assert lowered.risk == "high"
    assert any("min-retention" in w for w in lowered.warnings)
    assert assess(spec, "7", "14", ctx).risk == "low"
    snapshot_age = _spec("history.expire.max-snapshot-age-ms")
    assert assess(snapshot_age, None, str(DAY_MS), ctx).risk == "high"
    assert assess(snapshot_age, None, str(30 * DAY_MS), ctx).risk == "low"
    keep = _spec("history.expire.min-snapshots-to-keep")
    assert assess(keep, "5", "1", ctx).risk == "high"
    assert assess(keep, "1", "5", ctx).risk == "low"


def test_orphan_age_below_three_days_is_high() -> None:
    ctx = _ctx(cryo_preset_name="p", preset_params={"retention_days": 7})
    result = assess(_spec("cryo.orphan_retention_days"), "7", "2", ctx)
    assert result.risk == "high"
    assert any("in progress" in w for w in result.warnings)


def test_gc_direction() -> None:
    spec = _spec("gc.enabled")
    assert assess(spec, "false", "true", _ctx()).risk == "high"
    assert assess(spec, None, "false", _ctx()).risk == "medium"


def test_format_version_is_one_way_and_capped_by_pyiceberg() -> None:
    spec = _spec("format-version")
    assert assess(spec, "1", "2", _ctx(format_version=1)).risk == "high"
    assert assess(spec, "1", "2", _ctx(format_version=1)).blocked is None
    assert (
        assess(spec, "2", "1", _ctx(format_version=2)).blocked == "Iceberg has no format downgrade."
    )
    v3 = assess(spec, "2", "3", _ctx(format_version=2))
    assert v3.blocked and "Spark" in v3.blocked and "Trino" in v3.blocked


def test_merge_on_read_needs_v2_and_upsert_needs_identifier_fields() -> None:
    assert assess(_spec("write.merge.mode"), None, "merge-on-read", _ctx(format_version=1)).blocked
    assert assess(
        _spec("write.merge.mode"), None, "merge-on-read", _ctx(format_version=2)
    ).risk == ("medium")
    upsert = _spec("write.upsert.enabled")
    assert assess(upsert, None, "true", _ctx(format_version=2)).blocked
    allowed = assess(upsert, None, "true", _ctx(format_version=2, identifier_fields=("id",)))
    assert allowed.blocked is None and allowed.risk == "medium"


def test_target_size_below_cryo_target_is_medium_and_warns_about_reset() -> None:
    ctx = _ctx(cryo_preset_name="p", cryo_engine="native", preset_params={"file_size_mb": 256})
    result = assess(_spec("write.target-file-size-bytes"), None, str(64 * MIB), ctx)
    assert result.risk == "medium"
    assert any("resets this to 256 MiB" in w for w in result.warnings)
    trino = _ctx(cryo_preset_name="p", cryo_engine="trino", preset_params={"file_size_mb": 256})
    above = assess(_spec("write.target-file-size-bytes"), None, str(512 * MIB), trino)
    assert above.risk == "low" and not above.warnings


def test_normalize_enforces_bounds_and_types() -> None:
    ctx = _ctx(columns=("id", "ts"))
    target = _spec("write.target-file-size-bytes")
    assert normalize(target, "1024", ctx)[1]
    assert normalize(target, str(128 * MIB), ctx) == (str(128 * MIB), None)
    assert normalize(_spec("gc.enabled"), "TRUE", ctx) == ("true", None)
    assert normalize(_spec("gc.enabled"), "1", ctx)[1]
    metrics = _spec("write.metadata.metrics.default")
    assert normalize(metrics, "truncate(100)", ctx) == ("truncate(100)", None)
    assert normalize(metrics, "truncate(0)", ctx)[1]
    bloom = _spec("write.parquet.bloom-filter-columns")
    assert normalize(bloom, "ts, id", ctx) == ("id,ts", None)
    assert normalize(bloom, "nope", ctx)[1]
    assert normalize(_spec("format-version"), None, ctx)[1]


# ── Planner gate ─────────────────────────────────────────────────────────────


def _plan() -> MaintenancePlan:
    return MaintenancePlan(
        table_ref="c.s.t",
        mode=Mode.DEEP,
        operations=(
            Operation(OperationType.OPTIMIZE, {}),
            Operation(OperationType.EXPIRE_SNAPSHOTS, {}),
            Operation(OperationType.REMOVE_ORPHAN_FILES, {}),
        ),
    )


def test_gc_disabled_drops_destructive_operations_only() -> None:
    gated = gate_gc_disabled(_plan(), gc_disabled=True)
    assert [op.type for op in gated.operations] == [OperationType.OPTIMIZE]
    assert any("gc.enabled=false" in note for note in gated.notes)
    assert gate_gc_disabled(_plan(), gc_disabled=False) == _plan()


@pytest.mark.parametrize(
    ("raw", "disabled"),
    [
        ({"iceberg_properties": {"gc.enabled": "false"}}, True),
        ({"iceberg_properties": {"gc.enabled": "0"}}, True),
        ({"iceberg_properties": {"gc.enabled": "TRUE"}}, False),
        ({"iceberg_properties": {}}, False),
        ({"gc_enabled": False}, True),
        ({}, False),
    ],
)
def test_gc_disabled_uses_java_boolean_semantics(raw: dict, disabled: bool) -> None:
    assert gc_disabled_in_stats({"raw": raw}) is disabled


# ── Endpoints ────────────────────────────────────────────────────────────────


def _live(**overrides) -> LiveTableConfig:
    values = {
        "properties": {
            "write.target-file-size-bytes": str(128 * MIB),
            "write.parquet.bloom-filter-enabled.column.user_id": "true",
        },
        "format_version": 2,
        "table_uuid": "a1b2",
        "location": "s3://lake/analytics/events",
        "snapshot_count": 12,
        "column_count": 3,
        "bloom_candidate_columns": ("user_id", "event_date", "country"),
        "partition_fields": (SpecField("event_date_day", "event_date", "day"),),
        "refs": (RefInfo("main", "branch", 9), RefInfo("q3-close", "tag", 4)),
    }
    values.update(overrides)
    return LiveTableConfig(**values)


def _seed(session, *, engine: str = "spark_emr", assignment: bool = True) -> uuid.UUID:
    source_id, table_id, destination_id, preset_id = (uuid.uuid4() for _ in range(4))
    session.add(
        Source(id=source_id, name=f"lake-{source_id.hex[:6]}", type="rest", config={"uri": "x"})
    )
    session.add(
        Destination(
            id=destination_id,
            name=f"dest-{destination_id.hex[:6]}",
            engine=engine,
            config={"catalog_bindings": [{"source_id": str(source_id)}]},
        )
    )
    session.add(
        Table(
            id=table_id,
            source_id=source_id,
            schema_name="analytics",
            table_name="events",
            first_seen_at=datetime(2026, 10, 1, tzinfo=UTC),
            is_active=True,
        )
    )
    session.add(
        Preset(
            id=preset_id,
            name=f"compact-{preset_id.hex[:6]}",
            destination_id=destination_id,
            operations=["optimize", "expire_snapshots"],
            params={"file_size_mb": 256, "retention_days": 7},
            priority=100,
            guardrails={},
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=datetime(2026, 10, 1, tzinfo=UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=82,
            total_files=400,
            small_files_count=20,
            total_size_gb=12.0,
            total_partitions=40,
            total_snapshots=12,
            manifest_count=8,
            raw={"iceberg_properties": {"gc.enabled": "true"}, "format_version": 2},
        )
    )
    session.commit()
    if assignment:
        session.add(TableAssignment(table_id=table_id, preset_id=preset_id, pinned=True))
        session.commit()
    return table_id


def _settings(body: dict) -> dict[str, dict]:
    return {item["key"]: item for item in body["settings"]}


def test_get_config_reads_the_catalog(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed(session)
    session.close()
    with patch("cryo.table_config.live.read_live_config", return_value=_live()):
        response = api_db.client.get(
            f"/api/v1/tables/{table_id}/config?refresh=true", headers=VIEWER_HEADERS
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["read_from"] == "catalog" and body["can_edit"] is True
    assert body["preset_engine"] == "spark_emr"
    settings = _settings(body)
    target = settings["write.target-file-size-bytes"]
    assert target["current_value"] == str(128 * MIB)
    assert target["current_source"] == "table"
    assert target["recommended_value"] == str(256 * MIB)
    assert target["matches_recommendation"] is False
    assert settings["write.distribution-mode"]["recommended_value"] == "hash"
    assert settings["write.parquet.bloom-filter-columns"]["current_value"] == "user_id"
    retention = settings["cryo.retention_days"]
    assert retention["current_source"] == "cryo_preset"
    assert retention["min_role"] == "admin"
    lower = next(d for d in retention["risk_directions"] if d["label"] == "Lower")
    assert lower["risk"] == "high"
    assert settings["write.data.path"]["editable"] is False
    assert settings["write.data.path"]["change_risk"] == "high"
    tag = next(ref for ref in body["metadata"]["refs"] if ref["name"] == "q3-close")
    assert tag["retained_forever"] is True
    assert body["columns"] == ["user_id", "event_date", "country"]


def test_get_config_falls_back_to_the_scan(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed(session)
    session.close()
    with patch("cryo.table_config.live.read_live_config", side_effect=CatalogReadError("timeout")):
        response = api_db.client.get(
            f"/api/v1/tables/{table_id}/config?refresh=true", headers=VIEWER_HEADERS
        )
    assert response.status_code == 200
    body = response.json()
    assert body["read_from"] == "scan" and body["can_edit"] is False
    assert body["catalog_error"] == "timeout"
    settings = _settings(body)
    assert settings["gc.enabled"]["current_value"] == "true"
    assert settings["write.target-file-size-bytes"]["editable"] is False
    assert settings["cryo.retention_days"]["editable"] is True


def test_preview_flags_risk_and_blocks(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed(session)
    session.close()
    with patch("cryo.table_config.live.read_live_config", return_value=_live()):
        response = api_db.client.post(
            f"/api/v1/tables/{table_id}/config/preview",
            headers=VIEWER_HEADERS,
            json={
                "changes": [
                    {"key": "write.parquet.compression-codec", "value": "zstd"},
                    {"key": "gc.enabled", "value": "false"},
                    {"key": "format-version", "value": "3"},
                    {"key": "write.target-file-size-bytes", "value": str(128 * MIB)},
                ]
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()
    by_key = {c["key"]: c for c in body["changes"]}
    assert by_key["write.parquet.compression-codec"]["risk"] == "low"
    assert by_key["gc.enabled"]["risk"] == "medium"
    assert by_key["format-version"]["blocked"]
    assert by_key["write.target-file-size-bytes"]["no_op"] is True
    assert body["blocked"] is True


def _mock_catalog() -> tuple[MagicMock, MagicMock]:
    iceberg_table = MagicMock()
    txn = MagicMock()
    iceberg_table.transaction.return_value.__enter__ = MagicMock(return_value=txn)
    iceberg_table.transaction.return_value.__exit__ = MagicMock(return_value=False)
    return iceberg_table, txn


def test_apply_commits_one_transaction_and_audits_each_change(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed(session)
    session.close()
    iceberg_table, txn = _mock_catalog()
    after = _live(
        properties={
            "write.target-file-size-bytes": str(256 * MIB),
            "write.parquet.bloom-filter-enabled.column.country": "true",
            "write.distribution-mode": "hash",
        }
    )
    with (
        patch("cryo.table_config.live.read_live_config", side_effect=[_live(), after]),
        patch("cryo.table_config.live.load_iceberg_table", return_value=iceberg_table),
    ):
        response = api_db.client.post(
            f"/api/v1/tables/{table_id}/config/apply",
            headers=OPERATOR_HEADERS,
            json={
                "changes": [
                    {
                        "key": "write.target-file-size-bytes",
                        "value": str(256 * MIB),
                        "expected_value": str(128 * MIB),
                        "check_expected": True,
                    },
                    {"key": "write.distribution-mode", "value": "hash"},
                    {"key": "write.parquet.bloom-filter-columns", "value": "country"},
                ]
            },
        )
    assert response.status_code == 200, response.text
    txn.set_properties.assert_called_once_with(
        **{
            "write.target-file-size-bytes": str(256 * MIB),
            "write.distribution-mode": "hash",
            "write.parquet.bloom-filter-enabled.column.country": "true",
        }
    )
    txn.remove_properties.assert_called_once_with(
        "write.parquet.bloom-filter-enabled.column.user_id"
    )
    txn.upgrade_table_version.assert_not_called()
    body = response.json()
    assert len(body["audit_log_ids"]) == 3
    assert _settings(body["config"])["write.target-file-size-bytes"]["matches_recommendation"]

    session = api_db.session_factory()
    audits = session.query(AuditLog).filter(AuditLog.table_id == table_id).all()
    assert {a.action for a in audits} == {"table_config_change"}
    assert all(a.status == "OK" and a.engine == "pyiceberg" for a in audits)
    assert any("write.target-file-size-bytes: 134217728 -> 268435456" in a.detail for a in audits)
    stats = session.get(TableStats, table_id)
    assert stats.target_file_size_bytes == 256 * MIB
    assert stats.write_distribution_mode == "hash"
    session.close()


def test_apply_rejects_stale_expected_values(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed(session)
    session.close()
    with patch("cryo.table_config.live.read_live_config", return_value=_live()):
        response = api_db.client.post(
            f"/api/v1/tables/{table_id}/config/apply",
            headers=OPERATOR_HEADERS,
            json={
                "changes": [
                    {
                        "key": "write.target-file-size-bytes",
                        "value": str(256 * MIB),
                        "expected_value": str(64 * MIB),
                        "check_expected": True,
                    }
                ]
            },
        )
    assert response.status_code == 400
    assert "Changed since" in str(response.json()["detail"])


def test_high_risk_apply_needs_confirmation_and_reason(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed(session)
    session.close()
    iceberg_table, txn = _mock_catalog()
    change = {"changes": [{"key": "history.expire.max-snapshot-age-ms", "value": str(DAY_MS)}]}
    with (
        patch("cryo.table_config.live.read_live_config", return_value=_live()),
        patch("cryo.table_config.live.load_iceberg_table", return_value=iceberg_table),
    ):
        unconfirmed = api_db.client.post(
            f"/api/v1/tables/{table_id}/config/apply", headers=OPERATOR_HEADERS, json=change
        )
        no_reason = api_db.client.post(
            f"/api/v1/tables/{table_id}/config/apply",
            headers=OPERATOR_HEADERS,
            json={**change, "confirm": True, "reason": "ok"},
        )
        confirmed = api_db.client.post(
            f"/api/v1/tables/{table_id}/config/apply",
            headers=OPERATOR_HEADERS,
            json={**change, "confirm": True, "reason": "Storage budget review, ticket DE-42"},
        )
    assert unconfirmed.status_code == 400
    assert no_reason.status_code == 400
    assert confirmed.status_code == 200, confirmed.text
    txn.set_properties.assert_called_once_with(
        **{"history.expire.max-snapshot-age-ms": str(DAY_MS)}
    )
    session = api_db.session_factory()
    audit = session.query(AuditLog).filter(AuditLog.table_id == table_id).one()
    assert "reason=Storage budget review" in audit.detail and "risk=high" in audit.detail
    session.close()


def test_cryo_retention_override_is_admin_only(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed(session)
    session.close()
    change = {"changes": [{"key": "cryo.retention_days", "value": "14"}]}
    with patch("cryo.table_config.live.read_live_config", return_value=_live()):
        as_operator = api_db.client.post(
            f"/api/v1/tables/{table_id}/config/apply", headers=OPERATOR_HEADERS, json=change
        )
        as_admin = api_db.client.post(
            f"/api/v1/tables/{table_id}/config/apply", headers=ADMIN_HEADERS, json=change
        )
    assert as_operator.status_code == 403
    assert as_admin.status_code == 200, as_admin.text
    session = api_db.session_factory()
    assignment = session.get(TableAssignment, table_id)
    assert assignment.param_overrides == {"retention_days": 14}
    audit = session.query(AuditLog).filter(AuditLog.table_id == table_id).one()
    assert audit.engine == "cryo"
    session.close()
    assert (
        _settings(as_admin.json()["config"])["cryo.retention_days"]["current_source"]
        == "cryo_override"
    )


def test_failed_catalog_commit_is_audited(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed(session)
    session.close()
    iceberg_table = MagicMock()
    iceberg_table.transaction.side_effect = RuntimeError("CommitFailedException: conflict")
    with (
        patch("cryo.table_config.live.read_live_config", return_value=_live()),
        patch("cryo.table_config.live.load_iceberg_table", return_value=iceberg_table),
    ):
        response = api_db.client.post(
            f"/api/v1/tables/{table_id}/config/apply",
            headers=OPERATOR_HEADERS,
            json={"changes": [{"key": "commit.retry.num-retries", "value": "10"}]},
        )
    assert response.status_code == 502
    session = api_db.session_factory()
    audit = session.query(AuditLog).filter(AuditLog.table_id == table_id).one()
    assert audit.status == "FAILED" and "conflict" in audit.detail
    session.close()


def test_viewer_cannot_apply(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed(session)
    session.close()
    response = api_db.client.post(
        f"/api/v1/tables/{table_id}/config/apply",
        headers=VIEWER_HEADERS,
        json={"changes": [{"key": "commit.retry.num-retries", "value": "10"}]},
    )
    assert response.status_code == 403
