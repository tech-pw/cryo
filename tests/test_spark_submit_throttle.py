"""Per-destination spacing of Spark-on-Kubernetes submissions (#487)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from cryo.engines.spark.k8s import SparkK8sConfig
from cryo.engines.spark.submit_throttle import reserve_submit_delay


class _SlotRedis:
    """Mirrors _RESERVE_LUA: hand out max(now, next_free) and push next_free on."""

    def __init__(self) -> None:
        self.next_free: dict[str, float] = {}

    def eval(self, _script: str, _numkeys: int, key: str, now: float, interval: float) -> str:
        slot = max(now, self.next_free.get(key, 0.0))
        self.next_free[key] = slot + interval
        return str(slot)


def _no_jitter():
    return patch("cryo.engines.spark.submit_throttle.random.uniform", return_value=0.0)


def test_slots_are_spaced_one_interval_apart_across_callers() -> None:
    redis = _SlotRedis()
    with _no_jitter():
        delays = [
            reserve_submit_delay("dest-a", 60, redis_client=redis, now=lambda: 1000.0)
            for _ in range(3)
        ]
    assert delays == [0.0, 1.0, 2.0]


def test_destinations_are_throttled_independently() -> None:
    redis = _SlotRedis()
    with _no_jitter():
        reserve_submit_delay("dest-a", 60, redis_client=redis, now=lambda: 1000.0)
        assert reserve_submit_delay("dest-b", 60, redis_client=redis, now=lambda: 1000.0) == 0.0


def test_a_free_slot_after_idle_time_does_not_wait() -> None:
    redis = _SlotRedis()
    with _no_jitter():
        reserve_submit_delay("dest-a", 60, redis_client=redis, now=lambda: 1000.0)
        assert reserve_submit_delay("dest-a", 60, redis_client=redis, now=lambda: 1010.0) == 0.0


def test_zero_disables_throttling() -> None:
    assert reserve_submit_delay("dest-a", 0, redis_client=MagicMock()) == 0.0


def test_redis_error_falls_back_to_jitter_only() -> None:
    broken = MagicMock()
    broken.eval.side_effect = ConnectionError("down")
    delay = reserve_submit_delay("dest-a", 60, redis_client=broken)
    assert 0.0 <= delay <= 0.5


def test_config_reads_the_cap_and_defaults_to_one_per_second(monkeypatch) -> None:
    monkeypatch.delenv("CRYO_SPARK_K8S_MAX_SUBMITS_PER_MINUTE", raising=False)
    assert SparkK8sConfig.from_config({"namespace": "x"}).max_submits_per_minute == 60
    cfg = SparkK8sConfig.from_config({"namespace": "x", "spark_k8s": {"max_submits_per_minute": 0}})
    assert cfg.max_submits_per_minute == 0
