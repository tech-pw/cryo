"""Helpers for native partial-progress checkpoint/resume e2e (C9/C10).

These exercise the Go engine's ``executePartialProgressResumable`` path end-to-end:
each compaction group commits as its own atomic snapshot, a lost commit race only
forfeits the current group (reload + re-plan from the current snapshot), and an
exhausted race is surfaced as a first-class ``commit_race_lost`` signal.

See docs/native-engine-scoping-research.md §3.2 / Scenario B and
docs/native-iceberg-go-compaction-plan.md Phase E.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.native.payload import build_execute_request_for_operations
from cryo.engines.native.runner import (
    COMMIT_RACE_LOST_TOKEN,
    NativeExecuteResult,
    execute_request,
)
from tests.integration.native.rest_env import source_config


def partial_progress_params() -> dict[str, object]:
    """Optimize params that force per-group (partial-progress) commits.

    ``min_input_files=2`` keeps each identity partition (4 tiny files) eligible, so a
    3-partition fixture yields 3 groups and therefore 3 independent commits.
    """
    return {
        "target_file_size_bytes": 4 * 1024 * 1024,
        "min_file_size_bytes": 512 * 1024,
        "min_input_files": 2,
        "partial_progress": True,
    }


def build_partial_progress_request(table_ref: str) -> dict:
    plan = MaintenancePlan(
        table_ref=table_ref,
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.OPTIMIZE, partial_progress_params()),),
    )
    return build_execute_request_for_operations(
        plan=plan,
        operations=plan.operations,
        source_config=source_config(),
        destination_config={"operation_mode": "execute", "engine_impl": "go"},
    )


def run_partial_progress_optimize(
    table_ref: str,
    *,
    engine_config: Mapping[str, str],
    fault_env_vars: Mapping[str, str] | None = None,
    on_progress: Any = None,
    timeout_seconds: int = 300,
) -> NativeExecuteResult:
    """Run a partial-progress OPTIMIZE with optional fault-injection env + progress capture."""
    previous = {key: os.environ.get(key) for key in fault_env_vars or {}}
    try:
        if fault_env_vars:
            os.environ.update(fault_env_vars)
        return execute_request(
            build_partial_progress_request(table_ref),
            config=engine_config,
            on_progress=on_progress,
            timeout_seconds=timeout_seconds,
        )
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class CheckpointCollector:
    """Accumulate ``checkpoint`` progress events emitted per committed group."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, event: dict[str, Any]) -> None:
        if event.get("event") == "checkpoint":
            self.events.append(event)

    @property
    def committed_groups(self) -> list[int]:
        return [int(e["committed_groups"]) for e in self.events if e.get("committed_groups") is not None]

    @property
    def max_committed(self) -> int:
        groups = self.committed_groups
        return max(groups) if groups else 0


def is_commit_race_lost_result(result: NativeExecuteResult) -> bool:
    """True when the engine tagged the failure as an exhausted commit race."""
    haystacks: list[str] = []
    if result.error:
        haystacks.append(result.error)
    if result.stderr:
        haystacks.append(result.stderr)
    for item in result.results:
        if item.error:
            haystacks.append(item.error)
    return any(COMMIT_RACE_LOST_TOKEN in text for text in haystacks)
