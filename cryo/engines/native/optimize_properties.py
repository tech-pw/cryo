"""Persist write.target-file-size-bytes / write.distribution-mode after a native OPTIMIZE.

Mirrors cryo/engines/spark/sql.py's _render_optimize(), which prepends an
``ALTER TABLE ... SET TBLPROPERTIES`` statement so future writers inherit the target file
size Cryo just optimized toward. The native (Go engine) dispatch path had no equivalent --
confirmed live, 2026-07-27: schema.example_table kept failing the "Suboptimal
properties" health check after every native OPTIMIZE run, because nothing ever persisted
the property Cryo itself was already targeting during the rewrite.

Best-effort by design: the physical rewrite already succeeded by the time this runs, so a
failure to persist the property must not flip a successful job to failed.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from cryo.db.models import MaintenanceJob, Source, Table
from cryo.sources.pyiceberg_catalog import PyIcebergSourceCatalog

logger = logging.getLogger(__name__)

_DEFAULT_TARGET_FILE_SIZE_BYTES = 256 * 1024 * 1024


def maybe_persist_optimize_properties(session: Session, job: MaintenanceJob) -> bool | None:
    """Best-effort. Returns True/False if attempted, None if this job had no optimize step.

    Never raises -- logs and returns False on any failure so the caller's job/dispatch
    finalization is unaffected.
    """
    target_bytes = _optimize_target_bytes(job.plan)
    if target_bytes is None:
        return None
    try:
        table = session.get(Table, job.table_id)
        if table is None:
            return False
        source = session.get(Source, table.source_id)
        if source is None:
            return False
        catalog = PyIcebergSourceCatalog(name=str(source.id), config=source.config)
        iceberg_table = catalog.load_table(table.schema_name, table.table_name)
        with iceberg_table.transaction() as txn:
            txn.set_properties(
                **{
                    "write.target-file-size-bytes": str(target_bytes),
                    "write.distribution-mode": "hash",
                }
            )
        return True
    except Exception:
        logger.warning(
            "Best-effort optimize-property persistence failed for job %s (table %s): "
            "rewrite already succeeded, property persistence is skipped this run.",
            job.id,
            job.table_id,
            exc_info=True,
        )
        return False


def _optimize_target_bytes(plan: Any) -> int | None:
    if not isinstance(plan, dict):
        return None
    operations = plan.get("operations")
    if not isinstance(operations, list):
        return None
    for operation in operations:
        if not isinstance(operation, dict) or operation.get("type") != "optimize":
            continue
        params = operation.get("params")
        params = params if isinstance(params, dict) else {}
        raw = params.get("target_file_size_bytes")
        if raw is not None:
            try:
                value = int(raw)
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass
        file_size_mb = params.get("file_size_mb")
        if file_size_mb is not None:
            try:
                mb = int(file_size_mb)
                if mb > 0:
                    return mb * 1024 * 1024
            except (TypeError, ValueError):
                pass
        return _DEFAULT_TARGET_FILE_SIZE_BYTES
    return None
