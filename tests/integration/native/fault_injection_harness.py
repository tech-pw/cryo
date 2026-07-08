"""Helpers for native compaction fault-injection e2e tests (§8.5 C4–C8)."""

from __future__ import annotations

import os
from collections.abc import Mapping

from pyiceberg.table import Table

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.native.payload import build_execute_request_for_operations
from cryo.engines.native.runner import NativeExecuteResult, execute_request
from tests.integration.native.concurrency_harness import optimize_params
from tests.integration.native.rest_env import list_object_sizes, normalize_uri, source_config


def configure_commit_retries(table: Table, *, num_retries: int = 2) -> None:
    """Persist iceberg-go commit retry settings on the table for OCC retry tests."""
    with table.transaction() as txn:
        txn.set_properties(
            {
                "commit.retry.num-retries": str(num_retries),
                "commit.retry.min-wait-ms": "10",
                "commit.retry.max-wait-ms": "100",
                "commit.retry.total-timeout-ms": "5000",
            }
        )
        txn.commit_transaction()
    table.refresh()


def fault_env(**overrides: str) -> dict[str, str]:
    """Build env vars for Go testhook fault injection."""
    env = {key: value for key, value in overrides.items() if value}
    return env


def build_optimize_request(table_ref: str) -> dict:
    plan = MaintenancePlan(
        table_ref=table_ref,
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.OPTIMIZE, optimize_params()),),
    )
    return build_execute_request_for_operations(
        plan=plan,
        operations=plan.operations,
        source_config=source_config(),
        destination_config={"operation_mode": "execute", "engine_impl": "go"},
    )


def run_optimize_with_faults(
    table_ref: str,
    *,
    engine_config: Mapping[str, str],
    fault_env_vars: Mapping[str, str] | None = None,
    timeout_seconds: int = 300,
) -> NativeExecuteResult:
    env = os.environ.copy()
    if fault_env_vars:
        env.update(fault_env_vars)
    previous = {key: os.environ.get(key) for key in fault_env_vars or {}}
    try:
        if fault_env_vars:
            os.environ.update(fault_env_vars)
        return execute_request(
            build_optimize_request(table_ref),
            config=engine_config,
            timeout_seconds=timeout_seconds,
        )
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def build_remove_orphans_request(table_ref: str) -> dict:
    plan = MaintenancePlan(
        table_ref=table_ref,
        mode=Mode.DEEP,
        operations=(Operation(OperationType.REMOVE_ORPHAN_FILES, {"orphan_min_age_hours": 0}),),
    )
    return build_execute_request_for_operations(
        plan=plan,
        operations=plan.operations,
        source_config=source_config(),
        destination_config={"operation_mode": "execute", "engine_impl": "go"},
    )


def run_remove_orphans(
    table_ref: str,
    *,
    engine_config: Mapping[str, str],
    timeout_seconds: int = 300,
) -> NativeExecuteResult:
    return execute_request(
        build_remove_orphans_request(table_ref),
        config=engine_config,
        timeout_seconds=timeout_seconds,
    )


def is_commit_failure(result: NativeExecuteResult) -> bool:
    haystacks: list[str] = []
    if result.error:
        haystacks.append(result.error)
    if result.stderr:
        haystacks.append(result.stderr)
    for item in result.results:
        if item.error:
            haystacks.append(item.error)
    text = " ".join(haystacks).lower()
    needles = (
        "commit",
        "conflict",
        "retry",
        "failed",
        "diverged",
        "injected",
        "http 500",
    )
    return any(needle in text for needle in needles)


def data_parquet_paths(location: str) -> set[str]:
    sizes = list_object_sizes(location)
    return {
        normalize_uri(path)
        for path in sizes
        if path.endswith(".parquet") and "/data/" in path.lower()
    }


def orphan_data_paths(location: str, referenced_paths: set[str]) -> set[str]:
    return data_parquet_paths(location) - referenced_paths
