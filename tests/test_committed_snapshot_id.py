from cryo.maintenance.coordinator import _committed_snapshot_id


def test_committed_snapshot_id_keeps_a_long_decimal_string() -> None:
    snapshot_id = "9223372036854775807"
    assert _committed_snapshot_id({"committed_snapshot_id": snapshot_id}) == snapshot_id


def test_committed_snapshot_id_is_absent_when_the_engine_did_not_report_one() -> None:
    assert _committed_snapshot_id({}) is None
    assert _committed_snapshot_id({"committed_snapshot_id": "not-a-snapshot"}) is None
