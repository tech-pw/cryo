"""Iceberg v3 feature detection and optimization adjustments.

Detects Iceberg v3 features (deletion vectors, row lineage) and adjusts
maintenance strategies accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from cryo.db.models import Table, TableStats


@dataclass
class IcebergV3Features:
    """Detected Iceberg v3 features for a table."""

    table_id: str
    table_name: str
    format_version: int
    uses_deletion_vectors: bool
    has_row_lineage: bool
    single_file_commits: bool
    recommendations: list[str]


def detect_iceberg_v3_features(
    session: Session,
    table_id: str,
) -> IcebergV3Features:
    """
    Detect Iceberg v3 features for a table.
    
    Args:
        session: Database session
        table_id: Table UUID
    
    Returns:
        Iceberg v3 features detected
    """
    table = session.get(Table, table_id)
    if not table:
        raise ValueError(f"Table {table_id} not found")
    
    table_name = f"{table.catalog}.{table.schema_name}.{table.table_name}"
    
    stats = session.get(TableStats, table_id)
    if not stats or not stats.raw:
        # No metadata available
        return IcebergV3Features(
            table_id=str(table_id),
            table_name=table_name,
            format_version=2,  # Assume v2 if unknown
            uses_deletion_vectors=False,
            has_row_lineage=False,
            single_file_commits=False,
            recommendations=[],
        )
    
    raw_metadata = stats.raw if isinstance(stats.raw, dict) else {}
    
    # Detect format version
    format_version = raw_metadata.get("format_version", 2)
    
    # Detect deletion vectors (v3 feature)
    uses_deletion_vectors = False
    if format_version >= 3:
        # Check for delete files with deletion vectors
        delete_files = raw_metadata.get("delete_files", [])
        if delete_files:
            # Deletion vectors are marked with content type = "POSITION_DELETES" and file format contains "deletion-vectors"
            uses_deletion_vectors = any(
                isinstance(df, dict) and "deletion-vector" in str(df.get("file_path", "")).lower()
                for df in delete_files
            )
    
    # Detect row lineage (v3 feature)
    has_row_lineage = False
    if format_version >= 3:
        # Row lineage uses _row_id and _last_updated_sequence_number system columns
        schema = raw_metadata.get("schema", {})
        if isinstance(schema, dict):
            fields = schema.get("fields", [])
            field_names = {f.get("name", "").lower() for f in fields if isinstance(f, dict)}
            has_row_lineage = "_row_id" in field_names or "_last_updated_sequence_number" in field_names
    
    # Detect single-file commits (streaming pattern)
    single_file_commits = False
    snapshot_count = raw_metadata.get("snapshot_count", 0)
    total_files = raw_metadata.get("total_files", 0)
    if snapshot_count > 100 and total_files > 0:
        avg_files_per_commit = total_files / snapshot_count
        single_file_commits = avg_files_per_commit < 2.0
    
    # Generate recommendations
    recommendations = []
    
    if format_version >= 3:
        recommendations.append("✅ Iceberg v3 table - latest features available")
    else:
        recommendations.append("💡 Consider upgrading to Iceberg v3 for deletion vectors and row lineage")
    
    if uses_deletion_vectors:
        recommendations.append(
            "⚠️ Deletion vectors detected - skip eq-delete → pos-delete optimization (already optimal)"
        )
    
    if has_row_lineage:
        recommendations.append("✅ Row lineage enabled - CDC and audit use cases supported")
    
    if single_file_commits:
        recommendations.append(
            "⚠️ Streaming table detected (single-file commits) - increase compaction frequency to prevent file explosion"
        )
    
    if format_version < 3 and single_file_commits:
        recommendations.append(
            "🚀 Upgrade to v3 + enable deletion vectors for efficient streaming updates"
        )
    
    return IcebergV3Features(
        table_id=str(table_id),
        table_name=table_name,
        format_version=format_version,
        uses_deletion_vectors=uses_deletion_vectors,
        has_row_lineage=has_row_lineage,
        single_file_commits=single_file_commits,
        recommendations=recommendations,
    )


def should_skip_delete_optimization(features: IcebergV3Features) -> bool:
    """
    Check if delete file optimization should be skipped.
    
    Deletion vectors are more efficient than position deletes,
    so skip the eq-delete → pos-delete conversion.
    
    Args:
        features: Detected Iceberg v3 features
    
    Returns:
        True if delete optimization should be skipped
    """
    return features.uses_deletion_vectors


def adjust_compaction_frequency_for_streaming(
    features: IcebergV3Features,
    base_cooldown_days: int,
) -> int:
    """
    Adjust compaction cooldown for streaming tables.
    
    Tables with single-file commits need more frequent compaction
    to prevent file explosion.
    
    Args:
        features: Detected Iceberg v3 features
        base_cooldown_days: Normal cooldown period
    
    Returns:
        Adjusted cooldown period in days
    """
    if features.single_file_commits:
        # Reduce cooldown by 50% for streaming tables
        return max(1, base_cooldown_days // 2)
    
    return base_cooldown_days
