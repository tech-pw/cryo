from cryo.planner.planner import partition_has_write_signal


def test_missing_last_write_is_not_a_cold_signal() -> None:
    assert partition_has_write_signal({}) is False
    assert partition_has_write_signal({"last_write_at": None}) is False
    assert partition_has_write_signal({"updated_at": "2024-01-01T00:00:00Z"}) is False


def test_a_real_last_write_is_a_signal() -> None:
    assert partition_has_write_signal({"last_write_at": "2024-03-15T08:30:00.123Z"}) is True
