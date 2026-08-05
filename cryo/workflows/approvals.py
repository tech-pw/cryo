"""Approval workflow for destructive operations.

Requires manual approval before executing operations that can cause data loss:
- EXPIRE_SNAPSHOTS (deletes old snapshots)
- REMOVE_ORPHAN_FILES (permanent file deletion)
- DROP TABLE (if ever added)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Text, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.types import TIMESTAMP

from cryo.db.base import Base
from cryo.db.models import TimestampMixin
from cryo.domain.enums import OperationType


class ApprovalStatus(StrEnum):
    """Status of an approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalRequest(TimestampMixin, Base):
    """Approval request for a destructive operation."""

    __tablename__ = "approval_requests"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    table_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tables.id"), nullable=False
    )
    operation_type: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[str] = mapped_column(Text, nullable=False)  # User who requested
    approved_by: Mapped[str | None] = mapped_column(Text)  # User who approved/rejected
    status: Mapped[str] = mapped_column(Text, nullable=False, default=ApprovalStatus.PENDING)
    request_reason: Mapped[str | None] = mapped_column(Text)  # Why this operation is needed
    approval_reason: Mapped[str | None] = mapped_column(Text)  # Why approved/rejected
    impact_preview: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )  # Estimated impact
    operation_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )  # Operation parameters
    approved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    execution_job_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))


@dataclass
class ApprovalRequestData:
    """Data for creating an approval request."""

    table_id: UUID
    operation_type: OperationType
    requested_by: str
    request_reason: str | None
    impact_preview: dict[str, Any]
    operation_params: dict[str, Any]


def create_approval_request(
    session: Session,
    request_data: ApprovalRequestData,
    auto_expire_hours: int = 72,
) -> ApprovalRequest:
    """
    Create a new approval request.

    Args:
        session: Database session
        request_data: Approval request data
        auto_expire_hours: Hours until request expires (default: 72)

    Returns:
        Created approval request
    """
    from datetime import timedelta

    request = ApprovalRequest(
        table_id=request_data.table_id,
        operation_type=request_data.operation_type.value,
        requested_by=request_data.requested_by,
        request_reason=request_data.request_reason,
        impact_preview=request_data.impact_preview,
        operation_params=request_data.operation_params,
        status=ApprovalStatus.PENDING,
        expires_at=datetime.now(UTC) + timedelta(hours=auto_expire_hours),
    )

    session.add(request)
    session.commit()

    return request


def approve_request(
    session: Session,
    request_id: UUID,
    approved_by: str,
    approval_reason: str | None = None,
) -> ApprovalRequest:
    """
    Approve an approval request.

    Args:
        session: Database session
        request_id: Approval request ID
        approved_by: User who is approving
        approval_reason: Optional reason for approval

    Returns:
        Updated approval request

    Raises:
        ValueError: If request not found or already processed
    """
    request = session.get(ApprovalRequest, request_id)
    if not request:
        raise ValueError(f"Approval request {request_id} not found")

    if request.status != ApprovalStatus.PENDING:
        raise ValueError(f"Request already {request.status}")

    if request.expires_at and request.expires_at < datetime.now(UTC):
        request.status = ApprovalStatus.EXPIRED
        session.commit()
        raise ValueError("Request has expired")

    request.status = ApprovalStatus.APPROVED
    request.approved_by = approved_by
    request.approved_at = datetime.now(UTC)
    request.approval_reason = approval_reason

    session.flush()
    from cryo.config import get_settings

    settings = get_settings()
    if settings.durable_intents_enabled:
        from cryo.intents.binding import promote_intents_for_approval

        promote_intents_for_approval(
            session,
            table_id=request.table_id,
            operation=OperationType(request.operation_type),
            approved_by=approved_by,
            settings=settings,
        )
    session.commit()

    return request


def reject_request(
    session: Session,
    request_id: UUID,
    rejected_by: str,
    rejection_reason: str | None = None,
) -> ApprovalRequest:
    """
    Reject an approval request.

    Args:
        session: Database session
        request_id: Approval request ID
        rejected_by: User who is rejecting
        rejection_reason: Optional reason for rejection

    Returns:
        Updated approval request

    Raises:
        ValueError: If request not found or already processed
    """
    request = session.get(ApprovalRequest, request_id)
    if not request:
        raise ValueError(f"Approval request {request_id} not found")

    if request.status != ApprovalStatus.PENDING:
        raise ValueError(f"Request already {request.status}")

    request.status = ApprovalStatus.REJECTED
    request.approved_by = rejected_by
    request.approved_at = datetime.now(UTC)
    request.approval_reason = rejection_reason

    session.commit()

    return request


def get_pending_approvals(
    session: Session,
    table_id: UUID | None = None,
) -> list[ApprovalRequest]:
    """
    Get all pending approval requests.

    Args:
        session: Database session
        table_id: Optional table ID filter

    Returns:
        List of pending approval requests
    """
    stmt = select(ApprovalRequest).where(
        ApprovalRequest.status == ApprovalStatus.PENDING,
        ApprovalRequest.expires_at > datetime.now(UTC),
    )

    if table_id:
        stmt = stmt.where(ApprovalRequest.table_id == table_id)

    stmt = stmt.order_by(ApprovalRequest.created_at.desc())

    return list(session.execute(stmt).scalars().all())


def mark_as_executed(
    session: Session,
    request_id: UUID,
    job_id: UUID,
) -> ApprovalRequest:
    """
    Mark an approval request as executed.

    Args:
        session: Database session
        request_id: Approval request ID
        job_id: Job ID that executed the operation

    Returns:
        Updated approval request
    """
    request = session.get(ApprovalRequest, request_id)
    if not request:
        raise ValueError(f"Approval request {request_id} not found")

    if request.status != ApprovalStatus.APPROVED:
        raise ValueError(
            f"Request must be approved before execution (current status: {request.status})"
        )

    request.executed_at = datetime.now(UTC)
    request.execution_job_id = job_id

    session.commit()

    return request


def requires_approval(
    operation_type: OperationType,
    config_require_approval: dict[str, bool] | None = None,
) -> bool:
    """
    Check if an operation type requires approval.

    Args:
        operation_type: Operation type to check
        config_require_approval: Configuration dict with operation types requiring approval

    Returns:
        True if approval is required
    """
    if config_require_approval is None:
        # Default: require approval for destructive operations
        config_require_approval = {
            OperationType.EXPIRE_SNAPSHOTS: True,
            OperationType.REMOVE_ORPHAN_FILES: True,
        }

    return config_require_approval.get(operation_type, False)


def _parse_operation_type(value: str) -> OperationType | None:
    try:
        return OperationType(value)
    except ValueError:
        return None


def _is_parameter_safe(
    requested_params: dict[str, Any] | None,
    approved_params: dict[str, Any] | None,
) -> bool:
    """
    Check if the requested operation parameters are equal to or safer than approved ones.
    For EXPIRE_SNAPSHOTS and REMOVE_ORPHAN_FILES, retention_days defaults to 7.
    A larger retention_days is safer (retains more data).
    A larger retain_last is safer (retains more snapshots).
    """
    req = requested_params or {}
    app = approved_params or {}

    req_retention = int(req.get("retention_days", 7))
    app_retention = int(app.get("retention_days", 7))
    if req_retention < app_retention:
        return False

    app_retain_last = app.get("retain_last")
    if app_retain_last is not None:
        req_retain_last = req.get("retain_last")
        if req_retain_last is None or int(req_retain_last) < int(app_retain_last):
            return False

    return True


def _has_approved_request(
    session: Session,
    table_id: UUID,
    operation_type: OperationType,
    params: dict[str, Any] | None = None,
) -> bool:
    stmt = select(ApprovalRequest).where(
        ApprovalRequest.table_id == table_id,
        ApprovalRequest.operation_type == operation_type.value,
        ApprovalRequest.status == ApprovalStatus.APPROVED,
        ApprovalRequest.executed_at.is_(None),
    )
    requests = list(session.execute(stmt).scalars().all())
    for req in requests:
        if _is_parameter_safe(params, req.operation_params):
            return True
    return False


def job_requires_approval(
    session: Session,
    table_id: UUID,
    plan_operations: list[dict[str, Any]] | None,
) -> bool:
    """Return True if the job has destructive ops lacking an approved request."""
    if not plan_operations:
        return False

    for op in plan_operations:
        if not isinstance(op, dict):
            continue
        op_type = _parse_operation_type(str(op.get("type", "")))
        if op_type is None or not requires_approval(op_type):
            continue
        op_params = op.get("params") or {}
        if not _has_approved_request(session, table_id, op_type, op_params):
            return True

    return False


def expire_stale_approval_requests(session: Session) -> int:
    """Mark pending approval requests past expires_at as expired."""
    now = datetime.now(UTC)
    stmt = select(ApprovalRequest).where(
        ApprovalRequest.status == ApprovalStatus.PENDING,
        ApprovalRequest.expires_at.isnot(None),
        ApprovalRequest.expires_at <= now,
    )
    stale = list(session.execute(stmt).scalars().all())
    for request in stale:
        request.status = ApprovalStatus.EXPIRED
    if stale:
        session.commit()
    return len(stale)


def estimate_impact(
    session: Session,
    table_id: UUID,
    operation_type: OperationType,
    operation_params: dict[str, Any],
) -> dict[str, Any]:
    """
    Estimate the impact of a destructive operation.

    Args:
        session: Database session
        table_id: Table ID
        operation_type: Operation type
        operation_params: Operation parameters

    Returns:
        Impact preview dictionary
    """
    from cryo.db.model_utils import qualified_table_name
    from cryo.db.models import Table, TableStats

    table = session.get(Table, table_id)
    if not table:
        return {"error": "Table not found"}

    stats = session.get(TableStats, table_id)
    if not stats:
        return {"error": "No stats available"}

    table_name = qualified_table_name(table)

    if operation_type == OperationType.EXPIRE_SNAPSHOTS:
        retention_days = operation_params.get("retention_days", 7)
        snapshot_count = stats.total_snapshots or 0

        # Estimate snapshots to be deleted (rough estimate)
        snapshots_to_delete = max(0, snapshot_count - retention_days)

        return {
            "operation": "Expire Snapshots",
            "table_name": table_name,
            "current_snapshots": snapshot_count,
            "retention_days": retention_days,
            "estimated_snapshots_to_delete": snapshots_to_delete,
            "estimated_data_freed_gb": stats.total_size_gb * 0.2
            if snapshots_to_delete > 0
            else 0,  # Rough estimate
            "warning": "This operation is irreversible. Old snapshots will be permanently deleted.",
        }

    elif operation_type == OperationType.REMOVE_ORPHAN_FILES:
        orphan_retention_days = operation_params.get("retention_days", 7)

        return {
            "operation": "Remove Orphan Files",
            "table_name": table_name,
            "orphan_retention_days": orphan_retention_days,
            "estimated_orphan_files": "Unknown (requires scan)",
            "warning": (
                "This operation permanently deletes files not referenced by any "
                "snapshot or manifest."
            ),
        }

    return {
        "operation": operation_type.value,
        "table_name": table_name,
        "warning": "Impact estimation not available for this operation type.",
    }
