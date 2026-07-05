"""Maintenance-path ABAC enforcement helpers."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.api.security import Principal, Role
from cryo.domain.enums import OperationType
from cryo.security.abac import ABACPolicy

logger = logging.getLogger(__name__)


def _principal_role(principal: Principal | None) -> str:
    if principal is None:
        return "system"
    if Role.ADMIN in principal.roles:
        return Role.ADMIN.value
    if Role.OPERATOR in principal.roles:
        return Role.OPERATOR.value
    if Role.VIEWER in principal.roles:
        return Role.VIEWER.value
    return "viewer"


def _operation_key(operation: OperationType | str) -> str:
    if isinstance(operation, OperationType):
        return operation.value
    return str(operation).lower()


def evaluate_maintenance_allowed(
    session: Session,
    table_id: UUID,
    principal: Principal | None,
    operation: OperationType | str,
) -> tuple[bool, str | None]:
    """Return (allowed, denial_reason). Fail-open when no policy matches."""
    op_key = _operation_key(operation)
    role = _principal_role(principal)

    stmt = select(ABACPolicy).where(
        ABACPolicy.table_id == table_id,
        ABACPolicy.role == role,
    )
    policy = session.execute(stmt).scalar_one_or_none()
    if policy is None:
        return True, None

    allowed = policy.allowed_operations or []
    normalized = {str(item).lower() for item in allowed}
    if "maintenance" in normalized or op_key in normalized or "*" in normalized:
        return True, None

    reason = f"ABAC policy denies {op_key} for role {role} on table {table_id}"
    logger.warning(reason)
    return False, reason


def list_abac_policies(session: Session, table_id: UUID | None = None) -> list[ABACPolicy]:
    stmt = select(ABACPolicy)
    if table_id is not None:
        stmt = stmt.where(ABACPolicy.table_id == table_id)
    return list(session.execute(stmt).scalars().all())
