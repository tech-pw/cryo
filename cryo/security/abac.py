"""Cross-engine ABAC (Attribute-Based Access Control).

Fine-grained row-level and column-level security enforcement via
Iceberg REST Catalog Scan API interception.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Text, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from cryo.db.base import Base
from cryo.db.models import TimestampMixin


class ABACPolicy(TimestampMixin, Base):
    """ABAC policy for table-level access control."""

    __tablename__ = "abac_policies"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    table_id: Mapped[UUID] = mapped_column(nullable=False)  # Would add FK in production
    role: Mapped[str] = mapped_column(Text, nullable=False)  # "analyst", "engineer", etc.
    
    # Row-level filter (SQL condition)
    row_filter: Mapped[str | None] = mapped_column(Text)  # e.g., "region = current_user_region()"
    
    # Column-level masking
    column_masks: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # e.g., {"ssn": "REGEXP_REPLACE(ssn, '\\d{5}$', 'XXXXX')"}
    
    # Allowed operations
    allowed_operations: Mapped[list[str]] = mapped_column(JSONB, default=["SELECT"])
    # e.g., ["SELECT", "INSERT", "UPDATE", "DELETE"]


@dataclass
class ABACContext:
    """Context for ABAC policy evaluation."""

    user_id: str
    user_role: str
    user_attributes: dict[str, Any]  # e.g., {"region": "US", "department": "finance"}


def evaluate_row_filter(
    policy: ABACPolicy,
    context: ABACContext,
) -> str | None:
    """
    Evaluate row filter for a given context.
    
    Args:
        policy: ABAC policy
        context: User context
    
    Returns:
        SQL WHERE clause or None
    """
    if not policy.row_filter:
        return None
    
    # Replace placeholder functions with actual values
    filter_sql = policy.row_filter
    
    # Replace current_user_region() with actual user region
    if "current_user_region()" in filter_sql:
        user_region = context.user_attributes.get("region", "UNKNOWN")
        filter_sql = filter_sql.replace("current_user_region()", f"'{user_region}'")
    
    # Replace current_user_department() with actual department
    if "current_user_department()" in filter_sql:
        user_dept = context.user_attributes.get("department", "UNKNOWN")
        filter_sql = filter_sql.replace("current_user_department()", f"'{user_dept}'")
    
    return filter_sql


def apply_column_masks(
    policy: ABACPolicy,
    query_columns: list[str],
) -> dict[str, str]:
    """
    Apply column masking to query projection.
    
    Args:
        policy: ABAC policy
        query_columns: Columns being selected
    
    Returns:
        Mapping of original column to masked expression
    """
    masked_columns = {}
    
    for col in query_columns:
        if col in policy.column_masks:
            # Apply mask expression
            masked_columns[col] = policy.column_masks[col]
        else:
            # No mask, return column as-is
            masked_columns[col] = col
    
    return masked_columns


def intercept_scan_request(
    session: Session,
    table_id: UUID,
    context: ABACContext,
    requested_columns: list[str],
) -> dict[str, Any]:
    """
    Intercept Iceberg REST Catalog scan request and apply ABAC policies.
    
    This would be called by Cryo when acting as a REST catalog proxy.
    
    Args:
        session: Database session
        table_id: Table being scanned
        context: User context
        requested_columns: Columns user wants to scan
    
    Returns:
        Modified scan parameters with filters and projections
    """
    # Find applicable policy
    stmt = select(ABACPolicy).where(
        ABACPolicy.table_id == table_id,
        ABACPolicy.role == context.user_role,
    )
    
    policy = session.execute(stmt).scalar_one_or_none()
    
    if not policy:
        # No policy - allow full access (or deny based on default policy)
        return {
            "row_filter": None,
            "column_masks": {},
        }
    
    # Evaluate row filter
    row_filter = evaluate_row_filter(policy, context)
    
    # Apply column masks
    column_masks = apply_column_masks(policy, requested_columns)
    
    return {
        "row_filter": row_filter,
        "column_masks": column_masks,
    }


# Integration notes:
#
# To implement full ABAC, Cryo must act as an Iceberg REST Catalog proxy:
#
# 1. Client (Trino/Spark) → Cryo REST Catalog Proxy → Upstream Catalog
# 2. Cryo intercepts scan requests
# 3. Cryo evaluates ABAC policies
# 4. Cryo injects row filters into scan plan
# 5. Cryo returns modified scan plan to client
# 6. Client processes only authorized data
#
# This requires implementing the full Iceberg REST Catalog spec:
# - GET /v1/config
# - GET /v1/namespaces
# - POST /v1/namespaces/{namespace}/tables
# - GET /v1/namespaces/{namespace}/tables/{table}
# - POST /v1/namespaces/{namespace}/tables/{table}/metrics
#
# For production, consider using:
# - Apache Polaris (has built-in ABAC)
# - Tabular (SaaS with ABAC)
# - Unity Catalog (Databricks, has ABAC)
#
# Or implement Cryo as REST catalog proxy (significant effort)
