from uuid import uuid4

from cryo.api.routes_engine_load import engine_load_rows


def test_engine_load_marks_throttled_when_slots_are_full() -> None:
    dest = uuid4()
    rows = engine_load_rows([(dest, "trino", "trino", {"per_replica_cap": 2})], {dest: 2})
    assert rows[0].slots_used == 2
    assert rows[0].slots_limit == 2
    assert rows[0].admission == "throttled"
    assert rows[0].live_probe == "not recorded"


def test_engine_load_does_not_invent_a_zero_when_unused() -> None:
    dest = uuid4()
    rows = engine_load_rows([(dest, "spark", "spark_emr", None)], {})
    assert rows[0].slots_used == 0
    assert rows[0].admission == "open"
    assert rows[0].slots_limit == 5
