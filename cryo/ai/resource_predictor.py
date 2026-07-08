"""Lightweight resource prediction for maintenance jobs (shadow-safe)."""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, TableStats


@dataclass(slots=True)
class ResourcePrediction:
    table_id: uuid.UUID
    predicted_p50_seconds: float
    predicted_p90_seconds: float
    recommended_cpu_cores: float
    recommended_memory_gb: float
    confidence: float
    samples: int
    shadow_mode: bool = True


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return float(ordered[idx])


def predict_resources_for_table(session: Session, table_id: uuid.UUID) -> ResourcePrediction:
    stats = session.get(TableStats, table_id)
    if stats is None:
        return ResourcePrediction(
            table_id=table_id,
            predicted_p50_seconds=0.0,
            predicted_p90_seconds=0.0,
            recommended_cpu_cores=1.0,
            recommended_memory_gb=2.0,
            confidence=0.0,
            samples=0,
            shadow_mode=True,
        )

    rows = list(
        session.scalars(
            select(AuditLog)
            .where(
                AuditLog.table_id == table_id,
                AuditLog.duration_ms.isnot(None),
                AuditLog.status == "OK",
            )
            .order_by(desc(AuditLog.ts))
            .limit(120)
        )
    )
    durations = [float((row.duration_ms or 0) / 1000.0) for row in rows if row.duration_ms]
    if not durations:
        # Cold-start heuristic from current table shape.
        files = float(stats.total_files or 0)
        size_gb = float(stats.total_size_gb or 0.0)
        delete_ratio = float(stats.delete_file_pct or 0.0)
        p50 = max(30.0, size_gb * 3.0 + files * 0.02 + delete_ratio * 10.0)
        p90 = p50 * 1.8
        cpu = min(8.0, max(1.0, round(size_gb / 128.0 + delete_ratio * 2.0, 1)))
        mem = min(32.0, max(2.0, round(size_gb / 32.0 + files / 50000.0, 1)))
        return ResourcePrediction(
            table_id=table_id,
            predicted_p50_seconds=p50,
            predicted_p90_seconds=p90,
            recommended_cpu_cores=cpu,
            recommended_memory_gb=mem,
            confidence=0.25,
            samples=0,
            shadow_mode=True,
        )

    p50 = _percentile(durations, 0.5)
    p90 = _percentile(durations, 0.9)
    median_files = statistics.median([float(row.input_files_count or 0) for row in rows] or [0.0])
    size_gb = float(stats.total_size_gb or 0.0)
    cpu = min(12.0, max(1.0, round(size_gb / 96.0 + median_files / 12000.0, 1)))
    mem = min(48.0, max(2.0, round(size_gb / 24.0 + median_files / 70000.0, 1)))
    confidence = min(1.0, len(durations) / 40.0)
    return ResourcePrediction(
        table_id=table_id,
        predicted_p50_seconds=p50,
        predicted_p90_seconds=p90,
        recommended_cpu_cores=cpu,
        recommended_memory_gb=mem,
        confidence=round(confidence, 3),
        samples=len(durations),
        shadow_mode=True,
    )
