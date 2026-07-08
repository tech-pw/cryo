"""Compaction performance assertions (§8.6 P2/P4/P5) for cryo-engine-go."""

from __future__ import annotations

import os

import pytest

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import Operation
from cryo.engines.native.payload import build_execute_request_for_operations
from cryo.engines.native.runner import is_cryo_engine_available, resolve_binary_path
from tests.integration.native.compaction_fixtures import (
    UNPARTITIONED_TABLE,
    ensure_compaction_fixtures,
)
from tests.integration.native.compaction_oracles import (
    assert_file_count_reduced,
    capture_table_snapshot,
)
from tests.integration.native.perf_harness import P4_MAX_RSS_BYTES, execute_request_with_peak_rss
from tests.integration.native.rest_env import source_config

requires_rest = pytest.mark.skipif(
    os.getenv("CRYO_REST_URI") is None,
    reason="Set CRYO_REST_URI for native REST perf tests",
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
    reason="perf suite requires cryo-engine-go (CRYO_ENGINE_IMPL=go)",
)

requires_engine = pytest.mark.skipif(
    not is_cryo_engine_available(_engine_config()),
    reason="cryo-engine binary not found",
)


def _optimize_params() -> dict[str, int]:
    return {
        "target_file_size_bytes": 4 * 1024 * 1024,
        "min_file_size_bytes": 512 * 1024,
        "min_input_files": 3,
    }


def _run_optimize(table_ref: str) -> None:
    request = build_execute_request_for_operations(
        table_ref=table_ref,
        source_config=source_config(),
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                _optimize_params(),
            ),
        ),
        mode=Mode.DEEP,
    )
    result, _peak_rss = execute_request_with_peak_rss(request, config=_engine_config())
    assert result.ok, result.error
    op = result.results[0]
    assert op.success


@pytest.fixture(scope="module")
def compaction_fixtures() -> dict[str, object]:
    return ensure_compaction_fixtures()


@requires_rest
@requires_engine
@requires_go_engine
@pytest.mark.integration
def test_p2_file_count_reduced_after_cow_optimize(compaction_fixtures: dict[str, object]) -> None:
    """P2/P8: small-file count strictly decreases after bin-pack compaction."""
    fixture = compaction_fixtures[UNPARTITIONED_TABLE]
    from pyiceberg.catalog import load_catalog

    from tests.integration.native.rest_env import catalog_properties

    namespace, name = fixture.table_ref.split(".", 1)  # type: ignore[attr-defined]
    table = load_catalog("rest", **catalog_properties()).load_table((namespace, name))
    before = capture_table_snapshot(table)

    _run_optimize(fixture.table_ref)  # type: ignore[attr-defined]

    table.refresh()
    after = capture_table_snapshot(table)
    assert_file_count_reduced(before, after)


@requires_rest
@requires_engine
@requires_go_engine
@pytest.mark.integration
def test_p4_bounded_memory_during_compaction(compaction_fixtures: dict[str, object]) -> None:
    """P4: compaction stays under RSS cap (streaming, not whole-partition buffering)."""
    fixture = compaction_fixtures[UNPARTITIONED_TABLE]
    request = build_execute_request_for_operations(
        table_ref=fixture.table_ref,  # type: ignore[attr-defined]
        source_config=source_config(),
        operations=(Operation(OperationType.OPTIMIZE, _optimize_params()),),
        mode=Mode.DEEP,
    )
    result, peak_rss = execute_request_with_peak_rss(request, config=_engine_config())
    assert result.ok, result.error
    assert peak_rss > 0, "expected measurable RSS while subprocess ran"
    assert peak_rss < P4_MAX_RSS_BYTES, (
        f"P4 RSS {peak_rss // (1024 * 1024)} MiB exceeded cap "
        f"{P4_MAX_RSS_BYTES // (1024 * 1024)} MiB"
    )


@requires_rest
@requires_engine
@requires_go_engine
@pytest.mark.integration
def test_p5_idempotent_second_optimize_is_near_noop(compaction_fixtures: dict[str, object]) -> None:
    """P5: second optimize on already-compacted table removes ~0 files."""
    fixture = compaction_fixtures[UNPARTITIONED_TABLE]
    from pyiceberg.catalog import load_catalog

    from tests.integration.native.rest_env import catalog_properties

    namespace, name = fixture.table_ref.split(".", 1)  # type: ignore[attr-defined]
    table = load_catalog("rest", **catalog_properties()).load_table((namespace, name))

    _run_optimize(fixture.table_ref)  # type: ignore[attr-defined]
    table.refresh()
    before_second = capture_table_snapshot(table)

    request = build_execute_request_for_operations(
        table_ref=fixture.table_ref,  # type: ignore[attr-defined]
        source_config=source_config(),
        operations=(Operation(OperationType.OPTIMIZE, _optimize_params()),),
        mode=Mode.DEEP,
    )
    result, _ = execute_request_with_peak_rss(request, config=_engine_config())
    assert result.ok, result.error
    op = result.results[0]
    assert op.success

    table.refresh()
    after_second = capture_table_snapshot(table)
    assert after_second.data_file_count == before_second.data_file_count
    input_files = op.input_files or 0
    output_files = op.output_files or 0
    assert input_files <= before_second.data_file_count
    assert output_files >= input_files or output_files == before_second.data_file_count
