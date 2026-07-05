"""Integration tests for workload replay rate limiting configuration."""

from __future__ import annotations

from uuid import uuid4

from cryo.testing.workload_replay import WorkloadReplayConfig


def test_workload_replay_config_has_rate_limiting_params():
    """WorkloadReplayConfig should expose concurrency and delay controls."""
    config = WorkloadReplayConfig(
        source_table_id=uuid4(),
        shadow_table_name="cryo_test_shadow",
    )

    assert hasattr(config, "max_concurrent")
    assert hasattr(config, "delay_between_queries_ms")


def test_workload_replay_config_default_max_concurrent():
    """Default max_concurrent should allow modest parallelism."""
    config = WorkloadReplayConfig(
        source_table_id=uuid4(),
        shadow_table_name="shadow_table",
    )

    assert config.max_concurrent == 5


def test_workload_replay_config_default_delay_between_queries():
    """Default delay_between_queries_ms should be zero (no throttling)."""
    config = WorkloadReplayConfig(
        source_table_id=uuid4(),
        shadow_table_name="shadow_table",
    )

    assert config.delay_between_queries_ms == 0


def test_workload_replay_config_custom_rate_limits():
    """Rate limiting parameters should be overridable at construction."""
    config = WorkloadReplayConfig(
        source_table_id=uuid4(),
        shadow_table_name="shadow_table",
        max_concurrent=10,
        delay_between_queries_ms=50,
        query_limit=500,
    )

    assert config.max_concurrent == 10
    assert config.delay_between_queries_ms == 50
    assert config.query_limit == 500


def test_workload_replay_config_default_query_limit():
    """Default query_limit should capture a substantial workload sample."""
    config = WorkloadReplayConfig(
        source_table_id=uuid4(),
        shadow_table_name="shadow_table",
    )

    assert config.query_limit == 1000
