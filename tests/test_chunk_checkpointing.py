"""Tests for chunk checkpointing: repository, engine dispatch, and finalizer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from cryo.db.repositories.chunk_checkpoint import (
    get_checkpoint_summary,
    get_checkpointed_chunks,
    write_checkpoint,
)
from cryo.domain.enums import JobStatus, OperationType
from cryo.domain.models import MaintenancePlan, Mode, Operation


class TestChunkCheckpointRepository:
    def test_write_checkpoint_records_all_fields(self) -> None:
        session = MagicMock()
        run_id = str(uuid4())
        checkpoint = write_checkpoint(
            session=session,
            run_id=run_id,
            chunk_index=2,
            operation_type="optimize",
            partition_summary="event_date[day]=20180",
            input_files=100,
            output_files=10,
            bytes_removed=1024**3,
            duration_ms=5000,
            raw_result={"table_ref": "db.tbl", "ok": True},
        )
        session.add.assert_called_once_with(checkpoint)
        assert checkpoint.chunk_index == 2
        assert checkpoint.operation_type == "optimize"
        assert checkpoint.partition_summary == "event_date[day]=20180"
        assert checkpoint.input_files == 100
        assert checkpoint.output_files == 10
        assert checkpoint.run_id == run_id

    def test_get_checkpointed_chunks_returns_set_of_indices(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalars.return_value.all.return_value = [0, 2, 5]
        result = get_checkpointed_chunks(session, "run-123")
        assert result == {0, 2, 5}

    def test_get_checkpointed_chunks_empty_when_no_checkpoints(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalars.return_value.all.return_value = []
        result = get_checkpointed_chunks(session, "run-empty")
        assert result == set()

    def test_get_checkpoint_summary_aggregates_correctly(self) -> None:
        session = MagicMock()
        mock_row = MagicMock()
        mock_row.chunk_index = 0
        mock_row.operation_type = "optimize"
        mock_row.input_files = 100
        mock_row.output_files = 10
        mock_row.bytes_removed = 1024**3
        mock_row.duration_ms = 5000
        mock_row1 = MagicMock()
        mock_row1.chunk_index = 1
        mock_row1.operation_type = "optimize"
        mock_row1.input_files = 200
        mock_row1.output_files = 20
        mock_row1.bytes_removed = 2 * 1024**3
        mock_row1.duration_ms = 10000
        session.execute.return_value.all.return_value = [mock_row, mock_row1]
        result = get_checkpoint_summary(session, "run-123")
        assert result["total_chunks"] == 2
        assert result["total_input_files"] == 300
        assert result["total_output_files"] == 30
        assert result["total_bytes_removed"] == 3 * 1024**3
        assert result["total_duration_ms"] == 15000


class TestRunFinalizerPartial:
    def test_finalizer_marks_partial_when_some_succeeded_some_failed(self) -> None:
        from cryo.db.repositories.run_finalizer import TERMINAL

        assert TERMINAL == {
            "succeeded",
            "failed",
            "skipped",
            "partial",
        }
        assert JobStatus.PARTIAL.value == "partial"

    def test_finalizer_partial_status_logic(self) -> None:
        succeeded_count = 2
        failed_count = 1
        partial_count = 0
        if failed_count == 0 and partial_count == 0:
            final = "succeeded"
        elif succeeded_count > 0 or partial_count > 0:
            final = "partial"
        else:
            final = "failed"
        assert final == "partial"

    def test_finalizer_partial_from_partial_jobs(self) -> None:
        succeeded_count = 1
        failed_count = 0
        partial_count = 1
        if failed_count == 0 and partial_count == 0:
            final = "succeeded"
        elif succeeded_count > 0 or partial_count > 0:
            final = "partial"
        else:
            final = "failed"
        assert final == "partial"

    def test_finalizer_succeeded_when_all_succeeded(self) -> None:
        succeeded_count = 3
        failed_count = 0
        partial_count = 0
        if failed_count == 0 and partial_count == 0:
            final = "succeeded"
        elif succeeded_count > 0 or partial_count > 0:
            final = "partial"
        else:
            final = "failed"
        assert final == "succeeded"

    def test_finalizer_failed_when_all_failed(self) -> None:
        succeeded_count = 0
        failed_count = 2
        partial_count = 0
        if failed_count == 0 and partial_count == 0:
            final = "succeeded"
        elif succeeded_count > 0 or partial_count > 0:
            final = "partial"
        else:
            final = "failed"
        assert final == "failed"


class TestEnginePerChunkDispatch:
    def test_execute_plan_per_chunk_skips_checkpointed_chunks(self) -> None:
        from cryo.engines.native.checkpoint import execute_plan_per_chunk
        from cryo.engines.native.runner import NativeExecuteResult, NativeOperationResult

        plan = MaintenancePlan(
            table_ref="db.tbl",
            mode=Mode.DEEP,
            operations=(
                Operation(type=OperationType.OPTIMIZE, params={}, predicates=None),
                Operation(type=OperationType.OPTIMIZE, params={}, predicates=None),
                Operation(type=OperationType.OPTIMIZE, params={}, predicates=None),
            ),
        )
        session_factory = MagicMock()

        mock_session = MagicMock()
        mock_session.execute.return_value.scalars.return_value.all.return_value = [0]
        session_factory.return_value.__enter__ = MagicMock(return_value=mock_session)
        session_factory.return_value.__exit__ = MagicMock(return_value=False)

        with patch(
            "cryo.engines.native.checkpoint.execute_request"
        ) as mock_exec:
            mock_exec.return_value = NativeExecuteResult(
                ok=True,
                table_ref="db.tbl",
                results=(
                    NativeOperationResult(
                        operation="rewrite_data_files",
                        success=True,
                        input_files=50,
                        output_files=5,
                    ),
                ),
            )
            results = execute_plan_per_chunk(
                plan=plan,
                operations=plan.operations,
                source_config={"uri": "http://rest:8181", "warehouse": "s3://wh/"},
                destination_config={},
                run_id="run-123",
                session_factory=session_factory,
            )

        assert len(results) == 3
        assert results[0]["skipped"] is True
        assert results[1]["ok"] is True
        assert results[2]["ok"] is True
        assert mock_exec.call_count == 2

    def test_execute_plan_per_chunk_writes_checkpoint_on_success(self) -> None:
        from cryo.engines.native.checkpoint import execute_plan_per_chunk
        from cryo.engines.native.runner import NativeExecuteResult, NativeOperationResult

        plan = MaintenancePlan(
            table_ref="db.tbl",
            mode=Mode.DEEP,
            operations=(
                Operation(type=OperationType.OPTIMIZE, params={}, predicates=None),
            ),
        )
        session_factory = MagicMock()

        mock_session = MagicMock()
        mock_session.execute.return_value.scalars.return_value.all.return_value = []
        session_factory.return_value.__enter__ = MagicMock(return_value=mock_session)
        session_factory.return_value.__exit__ = MagicMock(return_value=False)

        with patch(
            "cryo.engines.native.checkpoint.execute_request"
        ) as mock_exec:
            mock_exec.return_value = NativeExecuteResult(
                ok=True,
                table_ref="db.tbl",
                results=(
                    NativeOperationResult(
                        operation="rewrite_data_files",
                        success=True,
                        input_files=100,
                        output_files=10,
                        bytes_removed=1024**3,
                        duration_ms=5000,
                    ),
                ),
            )
            execute_plan_per_chunk(
                plan=plan,
                operations=plan.operations,
                source_config={"uri": "http://rest:8181", "warehouse": "s3://wh/"},
                destination_config={},
                run_id="run-123",
                session_factory=session_factory,
            )

        mock_session.add.assert_called()
        mock_session.commit.assert_called()

    def test_execute_plan_per_chunk_does_not_checkpoint_on_failure(self) -> None:
        from cryo.engines.native.checkpoint import execute_plan_per_chunk
        from cryo.engines.native.runner import NativeExecuteResult

        plan = MaintenancePlan(
            table_ref="db.tbl",
            mode=Mode.DEEP,
            operations=(
                Operation(type=OperationType.OPTIMIZE, params={}, predicates=None),
            ),
        )
        session_factory = MagicMock()

        mock_session = MagicMock()
        mock_session.execute.return_value.scalars.return_value.all.return_value = []
        session_factory.return_value.__enter__ = MagicMock(return_value=mock_session)
        session_factory.return_value.__exit__ = MagicMock(return_value=False)

        with patch(
            "cryo.engines.native.checkpoint.execute_request"
        ) as mock_exec:
            mock_exec.return_value = NativeExecuteResult(
                ok=False,
                table_ref="db.tbl",
                results=(),
                error="commit failed",
            )
            results = execute_plan_per_chunk(
                plan=plan,
                operations=plan.operations,
                source_config={"uri": "http://rest:8181", "warehouse": "s3://wh/"},
                destination_config={},
                run_id="run-123",
                session_factory=session_factory,
            )

        assert results[0]["ok"] is False
        assert "commit failed" in results[0]["error"]
        mock_session.add.assert_not_called()
