"""Flink engine for Iceberg table maintenance.

Uses Iceberg's native TableMaintenance API (Flink 1.20+) to run
compaction, snapshot expiration, and orphan cleanup as Flink streaming
operators — eliminating the need for a separate Spark cluster.

Reference: https://iceberg.apache.org/docs/latest/flink-maintenance/
"""

from cryo.engines.flink.engine import FlinkEngine
from cryo.engines.flink.config import FlinkMaintenanceConfig

__all__ = ["FlinkEngine", "FlinkMaintenanceConfig"]
