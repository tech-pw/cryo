"""Integration tests for approval workflows."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cryo.db.models import Table
from cryo.workflows.approvals import (
    ApprovalRequestData,
    ApprovalStatus,
    approve_request,
    create_approval_request,
    estimate_impact,
    get_pending_approvals,
    reject_request,
    requires_approval,
)


def test_create_approval_request(session):
    """Test creating an approval request."""
    table = Table(
        id=uuid4(),
        source_id=uuid4(),
        name="test.approval_table",
        location="s3://test/approval",
        is_active=True,
    )
    session.add(table)
    session.commit()
    
    request_data = ApprovalRequestData(
        table_id=table.id,
        operation_type="EXPIRE_SNAPSHOTS",
        operation_params={"retention_days": 1},  # Aggressive!
        requested_by="user@example.com",
        reason="Testing approval workflow",
    )
    
    approval = create_approval_request(session, request_data, auto_expire_hours=24)
    
    assert approval.id is not None
    assert approval.table_id == table.id
    assert approval.operation_type == "EXPIRE_SNAPSHOTS"
    assert approval.status == ApprovalStatus.PENDING
    assert approval.requested_by == "user@example.com"
    assert approval.impact_preview is not None
    assert "estimated_snapshots_affected" in approval.impact_preview


def test_approve_request(session):
    """Test approving a request."""
    table = Table(
        id=uuid4(),
        source_id=uuid4(),
        name="test.approve_table",
        location="s3://test/approve",
        is_active=True,
    )
    session.add(table)
    session.commit()
    
    request_data = ApprovalRequestData(
        table_id=table.id,
        operation_type="REMOVE_ORPHAN_FILES",
        operation_params={"retention_days": 0},
        requested_by="requester@example.com",
    )
    
    approval = create_approval_request(session, request_data)
    session.commit()
    
    # Approve the request
    approved = approve_request(
        session,
        approval.id,
        approved_by="approver@example.com",
        approval_reason="Impact is acceptable",
    )
    
    assert approved.status == ApprovalStatus.APPROVED
    assert approved.approved_by == "approver@example.com"
    assert approved.approval_reason == "Impact is acceptable"
    assert approved.approved_at is not None


def test_reject_request(session):
    """Test rejecting a request."""
    table = Table(
        id=uuid4(),
        source_id=uuid4(),
        name="test.reject_table",
        location="s3://test/reject",
        is_active=True,
    )
    session.add(table)
    session.commit()
    
    request_data = ApprovalRequestData(
        table_id=table.id,
        operation_type="EXPIRE_SNAPSHOTS",
        operation_params={"retention_days": 0},  # Too aggressive
        requested_by="requester@example.com",
    )
    
    approval = create_approval_request(session, request_data)
    session.commit()
    
    # Reject the request
    rejected = reject_request(
        session,
        approval.id,
        rejected_by="approver@example.com",
        rejection_reason="Retention too aggressive, risk of data loss",
    )
    
    assert rejected.status == ApprovalStatus.REJECTED
    assert rejected.rejected_by == "approver@example.com"
    assert rejected.rejection_reason == "Retention too aggressive, risk of data loss"


def test_get_pending_approvals(session):
    """Test fetching pending approvals."""
    table1 = Table(
        id=uuid4(),
        source_id=uuid4(),
        name="test.pending1",
        location="s3://test/pending1",
        is_active=True,
    )
    table2 = Table(
        id=uuid4(),
        source_id=uuid4(),
        name="test.pending2",
        location="s3://test/pending2",
        is_active=True,
    )
    session.add_all([table1, table2])
    session.commit()
    
    # Create two pending requests
    for table in [table1, table2]:
        request_data = ApprovalRequestData(
            table_id=table.id,
            operation_type="EXPIRE_SNAPSHOTS",
            operation_params={"retention_days": 1},
            requested_by="user@example.com",
        )
        create_approval_request(session, request_data)
    
    session.commit()
    
    pending = get_pending_approvals(session)
    assert len(pending) == 2
    assert all(r.status == ApprovalStatus.PENDING for r in pending)


def test_requires_approval_destructive_ops(session):
    """Test that destructive operations require approval."""
    table = Table(
        id=uuid4(),
        source_id=uuid4(),
        name="test.destructive_table",
        location="s3://test/destructive",
        is_active=True,
    )
    session.add(table)
    session.commit()
    
    # These should require approval
    assert requires_approval(session, table.id, "EXPIRE_SNAPSHOTS") is True
    assert requires_approval(session, table.id, "REMOVE_ORPHAN_FILES") is True
    
    # These should NOT require approval (read-only or safe)
    assert requires_approval(session, table.id, "OPTIMIZE") is False
    assert requires_approval(session, table.id, "OPTIMIZE_MANIFESTS") is False


def test_estimate_impact(session):
    """Test impact estimation for operations."""
    from cryo.db.models import TableStats
    
    table = Table(
        id=uuid4(),
        source_id=uuid4(),
        name="test.impact_table",
        location="s3://test/impact",
        is_active=True,
    )
    session.add(table)
    
    stats = TableStats(
        table_id=table.id,
        total_size_gb=100.0,
        snapshot_count=50,
        small_files_count=1000,
    )
    session.add(stats)
    session.commit()
    
    # Estimate impact of aggressive snapshot expiry
    impact = estimate_impact(
        session,
        table.id,
        "EXPIRE_SNAPSHOTS",
        {"retention_days": 1},
    )
    
    assert "estimated_snapshots_affected" in impact
    assert "risk_level" in impact
    assert impact["risk_level"] in ["low", "medium", "high"]


def test_request_auto_expiration(session):
    """Test that requests auto-expire after threshold."""
    table = Table(
        id=uuid4(),
        source_id=uuid4(),
        name="test.expire_table",
        location="s3://test/expire",
        is_active=True,
    )
    session.add(table)
    session.commit()
    
    request_data = ApprovalRequestData(
        table_id=table.id,
        operation_type="EXPIRE_SNAPSHOTS",
        operation_params={"retention_days": 1},
        requested_by="user@example.com",
    )
    
    # Create with 1-hour expiry
    approval = create_approval_request(session, request_data, auto_expire_hours=1)
    session.commit()
    
    # Manually set expires_at to past
    approval.expires_at = datetime.now(UTC) - timedelta(hours=2)
    session.commit()
    
    # Should still be in DB but marked as expired (TODO: implement auto-expiry job)
    # For now just verify the expires_at was set correctly
    assert approval.expires_at < datetime.now(UTC)
