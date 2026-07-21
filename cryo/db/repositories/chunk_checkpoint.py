"""ChunkCheckpoint repository for per-chunk native execution checkpointing."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import ChunkCheckpoint


def write_checkpoint(
    session: Session,
    run_id: str,
    chunk_index: int,
    operation_type: str,
    *,
    table_id: str | None = None,
    partition_summary: str | None = None,
    input_files: int | None = None,
    output_files: int | None = None,
    bytes_removed: int | None = None,
    duration_ms: int | None = None,
    raw_result: dict[str, Any] | None = None,
) -> ChunkCheckpoint:
    """Write a checkpoint after a chunk commits successfully to Iceberg."""
    import uuid as uuid_mod

    checkpoint = ChunkCheckpoint(
        run_id=run_id,
        table_id=uuid_mod.UUID(str(table_id)) if table_id else None,
        chunk_index=chunk_index,
        operation_type=operation_type,
        partition_summary=partition_summary,
        input_files=input_files,
        output_files=output_files,
        bytes_removed=bytes_removed,
        duration_ms=duration_ms,
        committed_at=datetime.now(UTC),
        raw_result=raw_result,
    )
    session.add(checkpoint)
    session.flush()
    return checkpoint


def get_checkpointed_chunks(
    session: Session,
    run_id: str,
    *,
    table_id: str | None = None,
) -> set[int]:
    """Return the set of chunk indices that have already checkpointed for a run."""
    import uuid as uuid_mod

    stmt = select(ChunkCheckpoint.chunk_index).where(ChunkCheckpoint.run_id == run_id)
    if table_id is not None:
        stmt = stmt.where(ChunkCheckpoint.table_id == uuid_mod.UUID(str(table_id)))
    else:
        stmt = stmt.where(ChunkCheckpoint.table_id.is_(None))
    rows = session.execute(stmt).scalars().all()
    return {int(idx) for idx in rows}


def get_checkpoint_summary(
    session: Session,
    run_id: str,
) -> dict[str, Any]:
    """Return a summary dict of checkpointed chunks for a run."""
    rows = session.execute(
        select(
            ChunkCheckpoint.chunk_index,
            ChunkCheckpoint.operation_type,
            ChunkCheckpoint.input_files,
            ChunkCheckpoint.output_files,
            ChunkCheckpoint.bytes_removed,
            ChunkCheckpoint.duration_ms,
        ).where(ChunkCheckpoint.run_id == run_id)
        .order_by(ChunkCheckpoint.chunk_index)
    ).all()
    total_input = sum(r.input_files or 0 for r in rows)
    total_output = sum(r.output_files or 0 for r in rows)
    total_bytes_removed = sum(r.bytes_removed or 0 for r in rows)
    total_duration = sum(r.duration_ms or 0 for r in rows)
    return {
        "checkpointed_chunks": [
            {
                "chunk_index": int(r.chunk_index),
                "operation_type": r.operation_type,
                "input_files": r.input_files,
                "output_files": r.output_files,
                "bytes_removed": r.bytes_removed,
                "duration_ms": r.duration_ms,
            }
            for r in rows
        ],
        "total_chunks": len(rows),
        "total_input_files": total_input,
        "total_output_files": total_output,
        "total_bytes_removed": total_bytes_removed,
        "total_duration_ms": total_duration,
    }
