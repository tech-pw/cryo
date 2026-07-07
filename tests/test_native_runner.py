"""Tests for cryo-engine subprocess runner and native payload helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from cryo.domain.enums import EngineType, Mode, OperationType, PartitionTransform
from cryo.domain.models import (
    MaintenancePlan,
    Operation,
    PartitionPredicate,
    PartitionPredicateTerm,
)
from cryo.engines.base import RenderedStatement
from cryo.engines.native.engine import NativeEngine, _exec_result_from_native, _supported_operations
from cryo.engines.native.payload import (
    build_execute_request,
    build_execute_request_for_operations,
    native_table_ref,
    operation_from_dict,
    operation_to_dict,
    source_catalog_config,
)
from cryo.engines.native.runner import (
    NativeExecuteResult,
    NativeOperationResult,
    execute_request,
    is_progress_event,
    split_stdout_lines,
)


def test_native_table_ref_strips_catalog_prefix() -> None:
    assert native_table_ref("rest.demo.orders") == "demo.orders"
    assert native_table_ref("demo.orders") == "demo.orders"


def test_native_table_ref_multi_level_namespace() -> None:
    assert native_table_ref("rest.org.schema.orders") == "org.schema.orders"


def test_build_execute_request_includes_source_catalog() -> None:
    plan = MaintenancePlan(
        table_ref="rest.demo.orders",
        mode=Mode.DEEP,
        operations=(
            Operation(OperationType.EXPIRE_SNAPSHOTS, {"retention_days": 7, "retain_last": 1}),
        ),
    )
    request = build_execute_request(
        plan=plan,
        operation=plan.operations[0],
        source_config={
            "uri": "http://localhost:8181",
            "warehouse": "s3://warehouse/",
            "s3.endpoint": "http://localhost:9000",
            "s3.access-key-id": "minioadmin",
            "s3.secret-access-key": "minioadmin",
        },
        destination_config={"operation_mode": "execute"},
    )
    assert request["table_ref"] == "demo.orders"
    assert request["catalog_uri"] == "http://localhost:8181"
    assert request["operations"][0]["type"] == "expire_snapshots"


def test_source_catalog_config_forwards_storage_and_auth() -> None:
    config = source_catalog_config(
        {
            "uri": "http://localhost:8181",
            "warehouse": "s3://warehouse/",
            "s3.endpoint": "http://localhost:9000",
            "s3.access-key-id": "minioadmin",
            "s3.secret-access-key": "minioadmin",
            "s3.path-style-access": "true",
            "s3.region": "us-east-1",
            "token": "test-token",
        }
    )
    assert config["catalog_properties"]["s3.path-style-access"] == "true"
    assert config["catalog_properties"]["s3.region"] == "us-east-1"
    assert config["catalog_properties"]["token"] == "test-token"


def test_operation_roundtrip_preserves_predicates() -> None:
    operation = Operation(
        OperationType.OPTIMIZE,
        {"min_small_files": 10},
        predicates=(
            PartitionPredicate(
                terms=(
                    PartitionPredicateTerm(
                        base_column="event_date",
                        transform=PartitionTransform.DAY,
                        value=19542,
                        col_type="date",
                    ),
                )
            ),
        ),
    )
    restored = operation_from_dict(operation_to_dict(operation))
    assert restored is not None
    assert restored.predicates is not None
    assert len(restored.predicates) == 1
    assert restored.predicates[0].terms[0].base_column == "event_date"


def test_build_execute_request_includes_predicates() -> None:
    plan = MaintenancePlan(
        table_ref="rest.demo.orders",
        mode=Mode.DEEP,
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                {"min_small_files": 10},
                predicates=(
                    PartitionPredicate(
                        terms=(
                            PartitionPredicateTerm(
                                base_column="event_date",
                                transform=PartitionTransform.DAY,
                                value=19542,
                                col_type="date",
                            ),
                        )
                    ),
                ),
            ),
        ),
    )
    request = build_execute_request(
        plan=plan,
        operation=plan.operations[0],
        source_config={"uri": "http://localhost:8181", "warehouse": "s3://warehouse/"},
        destination_config={"operation_mode": "analyze"},
    )
    assert request["operations"][0]["predicates"][0]["terms"][0]["base_column"] == "event_date"


def test_unsupported_native_op_maps_to_skipped_exec_result() -> None:
    stmt = RenderedStatement(
        action=OperationType.OPTIMIZE_MANIFESTS,
        sql="NATIVE:rewrite_manifests:demo.orders",
        engine=EngineType.NATIVE,
        payload={},
    )
    result = NativeExecuteResult(
        ok=True,
        table_ref="demo.orders",
        results=(
            NativeOperationResult(
                operation="rewrite_manifests",
                success=False,
                error=(
                    "rewrite_manifests execute is not available on pinned iceberg-rust "
                    "(RewriteManifestsAction commit path blocked upstream; issue #2556)"
                ),
                duration_ms=3,
            ),
        ),
    )
    exec_result = _exec_result_from_native(stmt, result, duration_ms=3)
    assert exec_result.ok
    assert exec_result.skipped
    assert "rewrite_manifests execute is not available" in (exec_result.detail or "").lower()


def test_rewrite_data_files_upstream_block_maps_to_skipped() -> None:
    stmt = RenderedStatement(
        action=OperationType.OPTIMIZE,
        sql="NATIVE:rewrite_data_files:demo.orders",
        engine=EngineType.NATIVE,
        payload={},
    )
    result = NativeExecuteResult(
        ok=True,
        table_ref="demo.orders",
        results=(
            NativeOperationResult(
                operation="rewrite_data_files",
                success=False,
                input_files=12,
                output_files=2,
                bytes_removed=48_000_000,
                error=(
                    "rewrite_data_files commit blocked upstream on pinned iceberg-rust "
                    "(RewriteFilesAction / EPIC #2269); staged 2 output file(s)"
                ),
                duration_ms=8,
            ),
        ),
    )
    exec_result = _exec_result_from_native(stmt, result, duration_ms=8)
    assert exec_result.ok
    assert exec_result.skipped
    assert exec_result.input_files_count == 12
    assert exec_result.output_files_count == 2


def test_normalize_optimize_params_maps_file_size_mb() -> None:
    from cryo.domain.enums import Mode, OperationType
    from cryo.domain.models import MaintenancePlan, Operation
    from cryo.engines.native.payload import build_execute_request

    plan = MaintenancePlan(
        table_ref="rest.demo.orders",
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.OPTIMIZE, {"file_size_mb": 512}),),
    )
    request = build_execute_request(
        plan=plan,
        operation=plan.operations[0],
        source_config={"uri": "http://localhost:8181", "warehouse": "s3://warehouse/"},
        destination_config={"operation_mode": "analyze"},
    )
    params = request["operations"][0]["params"]
    assert params["target_file_size_bytes"] == 512 * 1024 * 1024


def test_build_execute_request_forwards_compaction_execute_flag() -> None:
    from cryo.domain.enums import Mode, OperationType
    from cryo.domain.models import MaintenancePlan, Operation
    from cryo.engines.native.payload import build_execute_request_for_operations

    plan = MaintenancePlan(
        table_ref="rest.demo.orders",
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.OPTIMIZE, {"file_size_mb": 256}),),
    )
    request = build_execute_request_for_operations(
        plan=plan,
        operations=plan.operations,
        source_config={"uri": "http://localhost:8181", "warehouse": "s3://warehouse/"},
        destination_config={"operation_mode": "execute", "compaction_execute": True},
    )
    assert request["params"]["compaction_execute"] is True


def test_execute_request_parses_successful_stdout(tmp_path: Path) -> None:
    script = tmp_path / "fake-cryo-engine"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "sys.stdin.read()\n"
        "payload = {"
        "'ok': True, 'table_ref': 'demo.orders', 'results': ["
        "{'operation': 'expire_snapshots', 'success': True, "
        "'snapshots_expired': 2, 'duration_ms': 5}"
        "]}\n"
        "print(json.dumps(payload))\n"
    )
    script.chmod(0o755)

    result = execute_request(
        {
            "table_ref": "demo.orders",
            "catalog_uri": "http://localhost:8181",
            "warehouse": "s3://warehouse/",
            "mode": "execute",
            "operations": [],
        },
        config={"binary_path": str(script)},
    )
    assert result.ok
    assert result.results[0].snapshots_expired == 2


def test_native_engine_execute_uses_subprocess() -> None:
    engine = NativeEngine(
        config={"binary_path": "/tmp/cryo-engine"},
        source_config={
            "uri": "http://localhost:8181",
            "warehouse": "s3://warehouse/",
        },
    )
    plan = MaintenancePlan(
        table_ref="rest.demo.orders",
        mode=Mode.DEEP,
        operations=(Operation(OperationType.EXPIRE_SNAPSHOTS, {"retention_days": 7}),),
    )
    stmt = engine.render(plan)[0]
    fake_result = MagicMock()
    fake_result.ok = True
    fake_result.results = [
        MagicMock(
            success=True,
            duration_ms=10,
            files_rewritten=None,
            bytes_removed=None,
            error=None,
            operation="expire_snapshots",
        )
    ]
    fake_result.stdout = '{"ok": true}'
    fake_result.error = None
    with (
        patch("cryo.engines.native.engine.resolve_binary_path", return_value="/tmp/cryo-engine"),
        patch("cryo.engines.native.engine.execute_request", return_value=fake_result),
    ):
        result = engine.execute(stmt)
    assert result.ok


def test_build_execute_request_for_multiple_operations() -> None:
    plan = MaintenancePlan(
        table_ref="rest.demo.orders",
        mode=Mode.DEEP,
        operations=(
            Operation(OperationType.EXPIRE_SNAPSHOTS, {"retention_days": 7}),
            Operation(OperationType.REMOVE_ORPHAN_FILES, {"orphan_min_age_hours": 72}),
        ),
    )
    request = build_execute_request_for_operations(
        plan=plan,
        operations=plan.operations,
        source_config={"uri": "http://localhost:8181", "warehouse": "s3://warehouse/"},
        destination_config={"operation_mode": "execute"},
    )
    assert len(request["operations"]) == 2
    assert request["operations"][0]["type"] == "expire_snapshots"
    assert request["operations"][1]["type"] == "remove_orphan_files"


def test_split_stdout_lines() -> None:
    stdout = (
        'CRYO_PROGRESS:{"event":"started","operation":"expire_snapshots"}\n'
        '{"ok": true, "table_ref": "demo.orders", "results": []}\n'
    )
    progress_lines, response_lines = split_stdout_lines(stdout)
    assert len(progress_lines) == 1
    assert len(response_lines) == 1
    assert response_lines[0].startswith('{"ok"')


def test_is_progress_event_filters_tracing() -> None:
    assert is_progress_event({"event": "progress", "operation": "expire_snapshots"})
    assert not is_progress_event({"level": "INFO", "message": "starting maintenance execution"})


def test_execute_request_honest_failure_when_operation_not_successful(tmp_path: Path) -> None:
    script = tmp_path / "fake-cryo-engine"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "sys.stdin.read()\n"
        "payload = {\n"
        "  'ok': False,\n"
        "  'table_ref': 'demo.orders',\n"
        "  'results': [{\n"
        "    'operation': 'remove_orphan_files',\n"
        "    'success': False,\n"
        "    'error': 'not yet implemented',\n"
        "    'duration_ms': 3,\n"
        "  }],\n"
        "}\n"
        "print(json.dumps(payload))\n"
        "sys.exit(1)\n"
    )
    script.chmod(0o755)

    result = execute_request(
        {
            "table_ref": "demo.orders",
            "catalog_uri": "http://localhost:8181",
            "warehouse": "s3://warehouse/",
            "mode": "execute",
            "operations": [{"type": "remove_orphan_files", "params": {}}],
        },
        config={"binary_path": str(script)},
    )
    assert not result.ok
    assert result.results[0].success is False


def test_operation_result_maps_input_output_files() -> None:
    from cryo.engines.native.runner import _parse_operation_result

    parsed = _parse_operation_result(
        {
            "operation": "rewrite_data_files",
            "success": True,
            "input_files": 10,
            "output_files": 2,
            "duration_ms": 8,
        },
        fallback_duration_ms=8,
    )
    assert parsed.input_files == 10
    assert parsed.output_files == 2


def test_exec_result_maps_snapshots_expired() -> None:
    stmt = RenderedStatement(
        action=OperationType.EXPIRE_SNAPSHOTS,
        sql="NATIVE:expire_snapshots:demo.orders",
        engine=EngineType.NATIVE,
        payload={},
    )
    result = NativeExecuteResult(
        ok=True,
        table_ref="demo.orders",
        results=(
            NativeOperationResult(
                operation="expire_snapshots",
                success=True,
                snapshots_expired=4,
                duration_ms=12,
            ),
        ),
    )
    exec_result = _exec_result_from_native(stmt, result, duration_ms=12)
    assert exec_result.ok
    assert exec_result.snapshots_expired == 4


def test_native_render_excludes_compaction_and_manifests() -> None:
    engine = NativeEngine(config={})
    plan = MaintenancePlan(
        table_ref="rest.demo.orders",
        mode=Mode.DEEP,
        operations=(
            Operation(OperationType.EXPIRE_SNAPSHOTS, {"retention_days": 7}),
            Operation(OperationType.REMOVE_ORPHAN_FILES, {"orphan_min_age_hours": 72}),
            Operation(OperationType.OPTIMIZE, {"file_size_mb": 256}),
            Operation(OperationType.OPTIMIZE_MANIFESTS, {}),
        ),
    )
    statements = engine.render(plan)
    assert len(statements) == 1
    payload = statements[0].payload
    assert payload.get("batch") is True
    operations = payload.get("operations")
    assert isinstance(operations, list)
    rendered_types = {item["type"] for item in operations}
    assert rendered_types == {"expire_snapshots", "remove_orphan_files"}


def test_native_supported_operations_excludes_compaction() -> None:
    plan = MaintenancePlan(
        table_ref="rest.demo.orders",
        mode=Mode.DEEP,
        operations=(
            Operation(OperationType.EXPIRE_SNAPSHOTS, {"retention_days": 7}),
            Operation(OperationType.REMOVE_ORPHAN_FILES, {"orphan_min_age_hours": 72}),
            Operation(OperationType.OPTIMIZE, {"file_size_mb": 256}),
            Operation(OperationType.OPTIMIZE_MANIFESTS, {}),
        ),
    )
    supported = _supported_operations(plan)
    assert [operation.type for operation in supported] == [
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.REMOVE_ORPHAN_FILES,
    ]
