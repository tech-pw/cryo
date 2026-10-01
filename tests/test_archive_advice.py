from __future__ import annotations

import inspect
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from cryo.api.routes_archive import ArchivePlanRequest
from cryo.tiering.archive_advice import (
    NO_EVIDENCE_COPY,
    EvidenceWindow,
    PartitionAdvice,
    PartitionFacts,
    PartitionReads,
    TableContext,
    dry_run_plan,
    evaluate_partition,
    policy_from_record,
    resolve_policy,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)
TRUSTED = EvidenceWindow(ingestion_healthy=True, window_days=40, log_delay_days=2)
ZERO = PartitionReads(reads=0, bytes_read=0, observed=True)
OFFLINE = frozenset({"GLACIER", "DEEP_ARCHIVE"})


def _facts(**overrides: object) -> PartitionFacts:
    payload: dict[str, object] = {
        "key": "event_date=2025-06-03",
        "partition_value": "2025-06-03",
        "transform": "identity",
        "size_bytes": 10 * 1024**3,
        "file_count": 2,
        "small_files": 0,
        "last_write_at": datetime(2025, 6, 4, tzinfo=UTC),
    }
    payload.update(overrides)
    return PartitionFacts(**payload)  # type: ignore[arg-type]


def _policy(storage_class: str = "GLACIER_IR", **predicate: object):
    return policy_from_record(
        name="cdp-default",
        storage_class=storage_class,
        min_age_days=90,
        predicate={"min_quiet_days": 7, "min_idle_days": 35, **predicate},
    )


def _advice(**overrides: object) -> PartitionAdvice:
    advice = evaluate_partition(_facts(), _policy(), TRUSTED, ZERO, now=NOW)
    if not overrides:
        return advice
    values = advice.__dict__ | overrides
    return PartitionAdvice(**values)


def test_missing_write_signal_is_not_cold() -> None:
    advice = evaluate_partition(_facts(last_write_at=None), _policy(), TRUSTED, ZERO, now=NOW)
    assert advice.suggestion == "blocked"
    assert "no_signal" in advice.reason_codes
    assert advice.storage_class not in OFFLINE | {"GLACIER_IR"}
    assert advice.eligible is False


def test_unparsed_partition_is_not_cold() -> None:
    facts = PartitionFacts(key="events", partition_value="not-a-date")
    advice = evaluate_partition(facts, _policy(), TRUSTED, ZERO, now=NOW)
    assert advice.suggestion == "blocked"
    assert "no_signal" in advice.reason_codes
    assert advice.suggestion != "archive_instant"


def test_no_read_evidence_caps_at_auto() -> None:
    advice = evaluate_partition(_facts(), _policy(), EvidenceWindow(), PartitionReads(), now=NOW)
    assert advice.suggestion == "auto"
    assert advice.storage_class == "INTELLIGENT_TIERING"
    assert advice.evidence_copy == NO_EVIDENCE_COPY
    assert "no_read_evidence" in advice.reason_codes
    assert advice.net_saving_usd_month is None
    assert advice.eligible is False
    assert "GLACIER" not in advice.reason_codes


def test_zero_reads_with_evidence_can_suggest_glacier_ir() -> None:
    advice = evaluate_partition(_facts(), _policy(), TRUSTED, ZERO, now=NOW)
    assert advice.suggestion == "archive_instant"
    assert advice.storage_class == "GLACIER_IR"
    assert advice.eligible is True
    assert advice.net_saving_usd_month == pytest.approx(0.19)


def test_read_share_picks_cool_or_hot() -> None:
    cool = PartitionReads(
        reads=3,
        bytes_read=5 * 1024**3,
        last_read_at=datetime(2026, 6, 1, tzinfo=UTC),
        observed=True,
    )
    hot = PartitionReads(
        reads=8,
        bytes_read=15 * 1024**3,
        last_read_at=datetime(2026, 6, 1, tzinfo=UTC),
        observed=True,
    )
    cool_advice = evaluate_partition(_facts(), _policy(), TRUSTED, cool, now=NOW)
    hot_advice = evaluate_partition(_facts(), _policy(), TRUSTED, hot, now=NOW)
    assert cool_advice.suggestion == "cool"
    assert cool_advice.storage_class == "STANDARD_IA"
    assert hot_advice.suggestion == "hot"
    assert hot_advice.net_saving_usd_month is None


def test_recent_reads_stay_hot() -> None:
    reads = PartitionReads(
        reads=2,
        bytes_read=0,
        last_read_at=NOW - timedelta(days=12),
        observed=True,
    )
    advice = evaluate_partition(_facts(), _policy(), TRUSTED, reads, now=NOW)
    assert advice.suggestion == "hot"
    assert "recent_reads" in advice.reason_codes
    assert advice.storage_class != "GLACIER_IR"


def test_compaction_gates_block() -> None:
    backlog = evaluate_partition(_facts(small_files=4), _policy(), TRUSTED, ZERO, now=NOW)
    tiny = evaluate_partition(
        _facts(file_count=4, size_bytes=4 * 7 * 1024 * 1024),
        _policy(),
        TRUSTED,
        ZERO,
        now=NOW,
    )
    unknown = evaluate_partition(_facts(small_files=None), _policy(), TRUSTED, ZERO, now=NOW)
    assert backlog.suggestion == "blocked"
    assert "small_file_backlog" in backlog.reason_codes
    assert "avg_file_below_8mb" in tiny.reason_codes
    assert "no_compaction_signal" in unknown.reason_codes
    assert unknown.suggestion != "archive_instant"


def test_exclusion_and_pin_hot() -> None:
    policy = policy_from_record(
        name="gold",
        storage_class="GLACIER_IR",
        min_age_days=90,
        predicate={"exclusions": {"schemas": ["gold"], "tags": ["pin"]}},
    )
    excluded = evaluate_partition(
        _facts(),
        policy,
        TRUSTED,
        ZERO,
        TableContext(schema_name="gold", table_name="events"),
        now=NOW,
    )
    pinned = evaluate_partition(
        _facts(),
        _policy(),
        TRUSTED,
        ZERO,
        TableContext(pin_hot=True),
        now=NOW,
    )
    assert excluded.suggestion == "hot"
    assert "excluded" in excluded.reason_codes
    assert pinned.suggestion == "hot"
    assert "pin_hot" in pinned.reason_codes


def test_offline_policy_target_is_refused() -> None:
    policy = policy_from_record(name="old", storage_class="DEEP_ARCHIVE", min_age_days=30)
    advice = evaluate_partition(_facts(), policy, TRUSTED, ZERO, now=NOW)
    assert advice.storage_class not in OFFLINE
    assert advice.suggestion != "archive_instant"
    assert "offline_target_refused" in advice.reason_codes
    assert advice.net_saving_usd_month is None


def test_too_young_and_not_quiet() -> None:
    young = evaluate_partition(
        _facts(
            partition_value="2026-09-01",
            last_write_at=datetime(2026, 9, 1, tzinfo=UTC),
        ),
        _policy(),
        TRUSTED,
        ZERO,
        now=NOW,
    )
    restless = evaluate_partition(
        _facts(last_write_at=datetime(2026, 9, 28, tzinfo=UTC)),
        _policy(),
        TRUSTED,
        ZERO,
        now=NOW,
    )
    assert "too_young" in young.reason_codes
    assert young.suggestion == "blocked"
    assert "not_quiet" in restless.reason_codes
    assert restless.suggestion == "hot"


def test_age_from_day_transform_and_yyyymmdd() -> None:
    partition_day = date(2025, 8, 27)
    epoch_days = (partition_day - date(1970, 1, 1)).days
    day_advice = evaluate_partition(
        _facts(
            partition_value=str(epoch_days),
            transform="day",
            partition_values={"dt": epoch_days},
            base_column="dt",
        ),
        _policy(),
        TRUSTED,
        ZERO,
        now=NOW,
    )
    ymd_advice = evaluate_partition(
        _facts(partition_value="20250603", transform=None),
        _policy(),
        TRUSTED,
        ZERO,
        now=NOW,
    )
    assert day_advice.age_days == (NOW.date() - partition_day).days
    assert ymd_advice.age_days == (NOW.date() - date(2025, 6, 3)).days


def test_resolve_policy_prefers_table_scope() -> None:
    source = policy_from_record(name="source", storage_class="STANDARD_IA", min_age_days=30)
    table = policy_from_record(
        name="table",
        storage_class="GLACIER_IR",
        min_age_days=180,
        predicate={"scope": "table", "schema_name": "gold", "table_name": "events"},
    )
    resolved = resolve_policy([source, table], schema_name="gold", table_name="events")
    assert resolved is not None
    assert resolved.name == "table"


def test_dry_run_never_writes_and_does_not_select_unevidenced_glacier() -> None:
    unevidenced = evaluate_partition(
        _facts(), _policy(), EvidenceWindow(), PartitionReads(), now=NOW
    )
    plan = dry_run_plan([unevidenced], target_class="GLACIER_IR")
    statuses = {item.name: item.status for item in plan.guardrails}
    assert plan.dry_run is True
    assert plan.applied is False
    assert plan.writes is False
    assert plan.executes is False
    assert plan.partitions_selected == 0
    assert statuses["no_s3_writes"] == "pass"
    assert statuses["versioning"] == "not_checked"
    assert statuses["object_lock"] == "not_checked"
    assert statuses["kms"] == "not_checked"
    assert statuses["replication"] == "not_checked"


def test_dry_run_prices_a_measured_glacier_ir_plan() -> None:
    advice = evaluate_partition(_facts(), _policy(), TRUSTED, ZERO, now=NOW)
    plan = dry_run_plan([advice], target_class="GLACIER_IR")
    assert plan.partitions_selected == 1
    assert plan.object_count == 2
    assert plan.request_cost_usd == pytest.approx(0.00004)
    assert plan.byte_count == 10 * 1024**3


def test_unmeasured_plan_cost_is_none() -> None:
    advice = _advice(
        file_count=None,
        size_bytes=None,
        suggestion="archive_instant",
        eligible=True,
        reason_codes=(),
        storage_class="GLACIER_IR",
    )
    plan = dry_run_plan([advice], target_class="GLACIER_IR")
    assert plan.object_count is None
    assert plan.request_cost_usd is None
    assert plan.early_delete_exposure_usd is None


def test_offline_plan_target_fails_closed() -> None:
    plan = dry_run_plan([_advice()], target_class="DEEP_ARCHIVE")
    assert plan.partitions_selected == 0
    assert plan.writes is False
    assert plan.target_class is None
    assert any(
        item.name == "readable_classes_only" and item.status == "fail" for item in plan.guardrails
    )


def test_plan_request_rejects_page_ids() -> None:
    with pytest.raises(ValidationError):
        ArchivePlanRequest.model_validate({"partition_ids": ["page-1"]})


def test_archive_routes_do_not_write_s3() -> None:
    import cryo.api.routes_archive as routes

    source = inspect.getsource(routes)
    assert "CopyObject" not in source
    assert "boto3" not in source
    assert "session.commit" not in source
    assert "session.add" not in source
    assert "/tiering/apply" not in source
    assert "/tiering/restore" not in source
