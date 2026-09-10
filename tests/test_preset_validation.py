from __future__ import annotations

import pytest

from cryo.planner.guardrails import validate_guardrails, validate_params
from tests.auth_helpers import ADMIN_HEADERS
from tests.test_presets_api_db import ApiDb


def test_validate_params_accepts_known_defaults() -> None:
    assert not validate_params(
        {
            "min_small_files": 128,
            "chunk_size_gb": 100,
            "delete_ratio_trigger": 0,
            "compute_statistics": False,
        }
    )


def test_validate_params_rejects_invalid_values() -> None:
    errors = validate_params(
        {
            "chunk_size_gb": 0,
            "delete_ratio_trigger": 120,
            "compute_statistics": "yes",
        }
    )
    assert "params.chunk_size_gb must be at least 1" in errors
    assert "params.delete_ratio_trigger must be at most 100" in errors
    assert "params.compute_statistics must be a boolean" in errors


def test_validate_params_allows_unknown_keys() -> None:
    assert not validate_params({"custom_flag": True, "partition_col": "event_date"})


def test_validate_params_accepts_min_partition_small_files() -> None:
    """min_partition_small_files (2026-09-10, gold_dbt_pw_bq spread-optimize fix) -- the
    per-partition override of min_small_files, same non-negative-number validation class."""
    assert not validate_params({"min_partition_small_files": 1})
    assert not validate_params({"min_partition_small_files": 0})
    errors = validate_params({"min_partition_small_files": -1})
    assert "params.min_partition_small_files must be non-negative" in errors


def test_validate_guardrails_accepts_routing_and_preferences() -> None:
    assert not validate_guardrails(
        {
            "cooldown_hours": 24,
            "adaptive_routing": True,
            "engine_pool": ["trino-main", "spark-large"],
            "engine_preferences": {
                "spark_min_gb": 100,
                "native_min_gb": 50,
                "latency_p99_ms": 500,
            },
            "routing_rules": [
                {
                    "predicate": {"field": "total_size_gb", "op": ">", "value": 100},
                    "destination": "spark-large",
                }
            ],
        }
    )


def test_validate_guardrails_rejects_invalid_shapes() -> None:
    errors = validate_guardrails(
        {
            "deadline_minutes": 0,
            "adaptive_routing": "yes",
            "engine_pool": [""],
            "routing_rules": [{"predicate": {"field": "unknown", "op": ">", "value": 1}}],
        }
    )
    assert "guardrails.deadline_minutes must be at least 1" in errors
    assert "guardrails.adaptive_routing must be a boolean" in errors
    assert "guardrails.engine_pool must be a list of non-empty strings" in errors
    assert "guardrails.routing_rules[0].predicate.field is unsupported" in errors


@pytest.mark.parametrize(
    ("payload", "error_key"),
    [
        ({"params": {"chunk_size_gb": 0}}, "params"),
        ({"guardrails": {"cooldown_hours": -1}}, "guardrails"),
    ],
)
def test_preset_api_rejects_invalid_params_and_guardrails(
    api_db: ApiDb, payload: dict[str, object], error_key: str
) -> None:
    client = api_db.client
    destination_response = client.post(
        "/api/v1/destinations",
        headers=ADMIN_HEADERS,
        json={"name": "trino-main", "engine": "trino", "config": {"host": "trino"}},
    )
    assert destination_response.status_code == 201
    destination_id = destination_response.json()["id"]

    response = client.post(
        "/api/v1/presets",
        headers=ADMIN_HEADERS,
        json={
            "name": "invalid",
            "destination_id": destination_id,
            "operations": ["optimize"],
            **payload,
        },
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert error_key in detail
