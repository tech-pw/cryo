"""Throughput SLO for GB-scale native compaction on local MinIO."""

from __future__ import annotations

import os

import pytest

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.native.payload import build_execute_request_for_operations
from cryo.engines.native.runner import is_cryo_engine_available, resolve_binary_path
from tests.integration.native.compaction_fixtures import (
    PERF_DENSE_PARTITION_TABLE,
    ensure_perf_compaction_fixture,
)
from tests.integration.native.perf_harness import (
    THROUGHPUT_TARGET_SECONDS,
    append_perf_result,
    execute_request_with_metrics,
)
from tests.integration.native.rest_env import source_config

requires_rest = pytest.mark.skipif(
    os.getenv("CRYO_REST_URI") is None,
    reason="Set CRYO_REST_URI for native REST throughput tests",
)

requires_perf_fixture = pytest.mark.skipif(
    os.getenv("CRYO_RUN_PERF_THROUGHPUT") != "1",
    reason="Set CRYO_RUN_PERF_THROUGHPUT=1 to run GB-scale throughput (slow)",
)


def _engine_config() -> dict[str, str]:
    config: dict[str, str] = {}
    configured = os.getenv("CRYO_ENGINE_BINARY")
    if configured:
        config["binary_path"] = configured
    impl = os.getenv("CRYO_ENGINE_IMPL")
    if impl:
        config["engine_impl"] = impl
    if config:
        return config
    resolved = resolve_binary_path({})
    if resolved is None:
        return {}
    return {"binary_path": resolved}


def _is_go_engine() -> bool:
    impl = os.getenv("CRYO_ENGINE_IMPL", "").strip().lower()
    if impl == "go":
        return True
    return "cryo-engine-go" in _engine_config().get("binary_path", "")


requires_go_engine = pytest.mark.skipif(
    not _is_go_engine(),
    reason="throughput suite requires cryo-engine-go",
)

requires_engine = pytest.mark.skipif(
    not is_cryo_engine_available(_engine_config()),
    reason="cryo-engine binary not found",
)


def _optimize_params(*, tuned: bool) -> dict[str, object]:
    target = 64 * 1024 * 1024
    params: dict[str, object] = {
        "target_file_size_bytes": target,
        # Spark-style: rewrite files smaller than ~75% of target.
        "min_file_size_bytes": int(target * 0.75),
        "min_input_files": 2,
        "partial_progress": True,
        "scan_concurrency": 1,
    }
    if tuned:
        # Memory-first: cut per-pipeline RSS, then admit parallel groups under budget.
        group_conc = int(os.getenv("CRYO_PERF_GROUP_CONCURRENCY", "2"))
        budget = int(
            os.getenv(
                "CRYO_PERF_MEMORY_BUDGET_BYTES",
                str(int(os.getenv("CRYO_PERF_BASELINE_RSS_BYTES", str(1024 * 1024 * 1024)))),
            )
        )
        params.update(
            {
                "group_concurrency": group_conc,
                "compaction_memory_budget_bytes": budget,
                "memory_expansion_factor": int(os.getenv("CRYO_PERF_EXPANSION_FACTOR", "96")),
                "read_batch_size": 16_384,
                "parquet_row_group_limit": 65_536,
                "parquet_row_group_bytes": 32 * 1024 * 1024,
                "compression_codec": "zstd",
                "compression_level": 3,
                "metrics_mode": "truncate(16)",
            }
        )
    else:
        # Explicit iceberg-go-ish defaults so payload setdefaults cannot retune baseline.
        params.update(
            {
                "group_concurrency": 1,
                "read_batch_size": 131_072,
                "parquet_row_group_limit": 1_048_576,
                "parquet_row_group_bytes": 128 * 1024 * 1024,
            }
        )
    return params


@pytest.mark.integration
@requires_rest
@requires_engine
@requires_go_engine
@requires_perf_fixture
def test_native_compaction_throughput_local_minio() -> None:
    """Wall-time ≤20 min for dense table; peak RSS recorded for the loop.

    Re-seeds when CRYO_PERF_RESEED=1 so each iteration starts from fragmented files.
    """
    # Always reseed for a fair wall-time comparison (compacted table is too fast).
    os.environ.setdefault("CRYO_PERF_RESEED", "1")
    fixture = ensure_perf_compaction_fixture(force=True)
    assert fixture.name == PERF_DENSE_PARTITION_TABLE

    label = os.getenv("CRYO_PERF_LABEL", "tuned")
    tuned = label != "baseline"
    plan = MaintenancePlan(
        table_ref=fixture.table_ref,
        mode=Mode.DEEP,
        operations=(Operation(OperationType.OPTIMIZE, _optimize_params(tuned=tuned)),),
    )
    request = build_execute_request_for_operations(
        plan=plan,
        operations=plan.operations,
        source_config=source_config(),
        destination_config={"operation_mode": "execute", "compaction_execute": True},
    )
    timeout = int(os.getenv("CRYO_PERF_TIMEOUT_SECONDS", str(THROUGHPUT_TARGET_SECONDS + 600)))
    pprof_dir = os.getenv("CRYO_ENGINE_PPROF_DIR")
    config = _engine_config()
    env_extra = {}
    if pprof_dir:
        env_extra["CRYO_ENGINE_PPROF_DIR"] = pprof_dir
    native, metrics = execute_request_with_metrics(
        request,
        config=config,
        timeout_seconds=timeout,
        label=label,
        env=env_extra or None,
    )
    results_dir = os.getenv("CRYO_PERF_RESULTS_DIR", "tmp/native-compaction-perf")
    append_perf_result(metrics, results_dir=results_dir)

    assert native.ok, f"compaction failed: {native.error}\n{native.stderr}"
    baseline_rss = os.getenv("CRYO_PERF_BASELINE_RSS_BYTES")
    if baseline_rss and label.startswith("tuned"):
        cap = int(baseline_rss)
        assert metrics.peak_rss_bytes <= cap, (
            f"peak RSS {metrics.peak_rss_bytes} exceeds baseline cap {cap} (label={label})"
        )
    # Soft gate for small fixtures: only enforce 20 min when input ≥ 1 GiB.
    if metrics.input_bytes >= 1024 * 1024 * 1024:
        assert metrics.wall_seconds <= THROUGHPUT_TARGET_SECONDS, (
            f"wall {metrics.wall_seconds:.1f}s exceeds {THROUGHPUT_TARGET_SECONDS}s target "
            f"(MB/s={metrics.mb_per_second:.2f}, "
            f"peak_rss_mib={metrics.peak_rss_bytes // (1024 * 1024)})"
        )
    print(
        f"PERF {label}: wall={metrics.wall_seconds:.1f}s "
        f"peak_rss_mib={metrics.peak_rss_bytes // (1024 * 1024)} "
        f"input_mib={metrics.input_bytes // (1024 * 1024)} "
        f"mb_s={metrics.mb_per_second:.2f}"
    )
