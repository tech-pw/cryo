"""Helpers for native compaction concurrency e2e tests (§8.5 C2/C3)."""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.native.payload import build_execute_request_for_operations
from cryo.engines.native.runner import NativeExecuteResult, execute_request
from tests.integration.native.rest_env import source_config

REPO_ROOT = Path(__file__).resolve().parents[3]
SEED_BINARY_ENV = "CRYO_SEED_NATIVE_FIXTURES_BINARY"


def seed_binary() -> Path:
    configured = os.getenv(SEED_BINARY_ENV)
    if configured:
        return Path(configured)
    return REPO_ROOT / "engine-go" / "bin" / "seed-native-fixtures"


def reseed_mor_table(table_name: str) -> None:
    binary = seed_binary()
    if not binary.is_file():
        raise FileNotFoundError(
            f"MOR fixture seeder not found at {binary}; run `make build-seed-native-fixtures`"
        )
    subprocess.run(
        [str(binary), "seed-table", table_name],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


def commit_equality_delete(table_ref: str, deleted_ids: set[int]) -> None:
    binary = seed_binary()
    if not binary.is_file():
        raise FileNotFoundError(f"MOR fixture seeder not found at {binary}")
    payload = ",".join(str(value) for value in sorted(deleted_ids))
    subprocess.run(
        [str(binary), "commit-equality-delete", table_ref, payload],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


def optimize_params() -> dict[str, int]:
    return {
        "target_file_size_bytes": 4 * 1024 * 1024,
        "min_file_size_bytes": 512 * 1024,
        "min_input_files": 1,
    }


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


def run_optimize(
    table_ref: str,
    *,
    engine_config: dict[str, str],
    extra_env: dict[str, str] | None = None,
    on_progress=None,
    timeout_seconds: int = 300,
) -> NativeExecuteResult:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    previous = {key: os.environ.get(key) for key in extra_env or {}}
    try:
        if extra_env:
            os.environ.update(extra_env)
        return execute_request(
            build_optimize_request(table_ref),
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


@dataclass
class AsyncOptimize:
    thread: threading.Thread
    result: NativeExecuteResult | None = None
    error: BaseException | None = None


def start_optimize_async(
    table_ref: str,
    *,
    engine_config: dict[str, str],
    extra_env: dict[str, str] | None = None,
    on_progress=None,
) -> AsyncOptimize:
    holder = AsyncOptimize(thread=threading.Thread())

    def _target() -> None:
        try:
            holder.result = run_optimize(
                table_ref,
                engine_config=engine_config,
                extra_env=extra_env,
                on_progress=on_progress,
            )
        except BaseException as exc:  # noqa: BLE001 - propagate to test
            holder.error = exc

    holder.thread = threading.Thread(target=_target, daemon=True)
    holder.thread.start()
    return holder


def join_optimize(holder: AsyncOptimize, *, timeout_seconds: float = 300) -> NativeExecuteResult:
    holder.thread.join(timeout=timeout_seconds)
    if holder.thread.is_alive():
        raise TimeoutError("compaction did not finish before timeout")
    if holder.error is not None:
        raise holder.error
    assert holder.result is not None
    return holder.result


def is_commit_conflict_error(result: NativeExecuteResult) -> bool:
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
        "diverged",
        "failed",
        "retry",
        "concurrent",
        "serializable",
    )
    return any(needle in text for needle in needles)
