from types import SimpleNamespace
from uuid import uuid4

import pytest

from cryo.api import routes_engine_load
from cryo.api.routes_engine_load import engine_load_rows, workers_online


def test_workers_online_reads_consumer_group(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = SimpleNamespace(active_worker_count=lambda: 3)
    monkeypatch.setattr(
        "cryo.infra.wiring.build_runtime_wiring", lambda: SimpleNamespace(queue=queue)
    )
    assert workers_online() == 3


def test_workers_online_is_unknown_not_zero_when_redis_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> None:
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr("cryo.infra.wiring.build_runtime_wiring", broken)
    assert routes_engine_load.workers_online() is None


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
