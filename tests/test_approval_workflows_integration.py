"""Integration tests for approval workflows."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import Source, Table, TableStats
from cryo.domain.enums import OperationType
from cryo.workflows.approvals import (
    ApprovalRequestData,
    ApprovalStatus,
    approve_request,
    create_approval_request,
    estimate_impact,
    expire_stale_approval_requests,
    get_pending_approvals,
    job_requires_approval,
    reject_request,
    requires_approval,
)


def _seed_source(session) -> Source:
    source = Source(
        id=uuid4(),
        name=f"source-{uuid4()}",
        type="rest",
        config={"catalog_name": "iceberg"},
    )
    session.add(source)
    session.flush()
    return source


def _seed_table(session, *, schema_name: str, table_name: str) -> Table:
    source = _seed_source(session)
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name=schema_name,
        table_name=table_name,
        is_active=True,
    )
    session.add(table)
    session.flush()
    return table


def _approval_request_data(
    session,
    table: Table,
    operation_type: OperationType,
    *,
    operation_params: dict | None = None,
    requested_by: str = "user@example.com",
    request_reason: str | None = None,
) -> ApprovalRequestData:
    params = operation_params or {}
    return ApprovalRequestData(
        table_id=table.id,
        operation_type=operation_type,
        requested_by=requested_by,
        request_reason=request_reason,
        impact_preview=estimate_impact(session, table.id, operation_type, params),
        operation_params=params,
    )


def test_create_approval_request(session):
    """Test creating an approval request."""
    table = _seed_table(session, schema_name="test", table_name="approval_table")
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=True,
            compaction_priority="HIGH",
            grade="C",
            overall_score=50,
            total_files=100,
            small_files_count=50,
            total_size_gb=100.0,
            total_snapshots=50,
        )
    )
    session.commit()

    request_data = _approval_request_data(
        session,
        table,
        OperationType.EXPIRE_SNAPSHOTS,
        operation_params={"retention_days": 1},
        request_reason="Testing approval workflow",
    )

    approval = create_approval_request(session, request_data, auto_expire_hours=24)

    assert approval.id is not None
    assert approval.table_id == table.id
    assert approval.operation_type == OperationType.EXPIRE_SNAPSHOTS.value
    assert approval.status == ApprovalStatus.PENDING
    assert approval.requested_by == "user@example.com"
    assert approval.impact_preview is not None
    assert "estimated_snapshots_to_delete" in approval.impact_preview


def test_approve_request(session):
    """Test approving a request."""
    table = _seed_table(session, schema_name="test", table_name="approve_table")
    session.commit()

    request_data = _approval_request_data(
        session,
        table,
        OperationType.REMOVE_ORPHAN_FILES,
        operation_params={"retention_days": 0},
        requested_by="requester@example.com",
    )

    approval = create_approval_request(session, request_data)
    session.commit()

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
    table = _seed_table(session, schema_name="test", table_name="reject_table")
    session.commit()

    request_data = _approval_request_data(
        session,
        table,
        OperationType.EXPIRE_SNAPSHOTS,
        operation_params={"retention_days": 0},
        requested_by="requester@example.com",
    )

    approval = create_approval_request(session, request_data)
    session.commit()

    rejected = reject_request(
        session,
        approval.id,
        rejected_by="approver@example.com",
        rejection_reason="Retention too aggressive, risk of data loss",
    )

    assert rejected.status == ApprovalStatus.REJECTED
    assert rejected.approved_by == "approver@example.com"
    assert rejected.approval_reason == "Retention too aggressive, risk of data loss"


def test_get_pending_approvals(session):
    """Test fetching pending approvals."""
    table1 = _seed_table(session, schema_name="test", table_name="pending1")
    table2 = _seed_table(session, schema_name="test", table_name="pending2")
    session.commit()

    for table in [table1, table2]:
        create_approval_request(
            session,
            _approval_request_data(
                session,
                table,
                OperationType.EXPIRE_SNAPSHOTS,
                operation_params={"retention_days": 1},
            ),
        )

    session.commit()

    pending = get_pending_approvals(session)
    assert len(pending) == 2
    assert all(r.status == ApprovalStatus.PENDING for r in pending)


def test_requires_approval_destructive_ops():
    """Test that destructive operations require approval."""
    assert requires_approval(OperationType.EXPIRE_SNAPSHOTS) is True
    assert requires_approval(OperationType.REMOVE_ORPHAN_FILES) is True
    assert requires_approval(OperationType.OPTIMIZE) is False
    assert requires_approval(OperationType.OPTIMIZE_MANIFESTS) is False


def test_job_requires_approval_without_approved_request(session):
    """Destructive plan ops defer until an approved request exists."""
    table = _seed_table(session, schema_name="test", table_name="destructive_table")
    session.commit()

    plan_operations = [{"type": OperationType.EXPIRE_SNAPSHOTS.value, "params": {}}]
    assert job_requires_approval(session, table.id, plan_operations) is True

    approval = create_approval_request(
        session,
        _approval_request_data(
            session,
            table,
            OperationType.EXPIRE_SNAPSHOTS,
            operation_params={"retention_days": 7},
        ),
    )
    approve_request(session, approval.id, approved_by="approver@example.com")
    session.commit()

    assert job_requires_approval(session, table.id, plan_operations) is False


def test_estimate_impact(session):
    """Test impact estimation for operations."""
    table = _seed_table(session, schema_name="test", table_name="impact_table")
    stats = TableStats(
        table_id=table.id,
        scan_at=datetime.now(UTC),
        needs_compaction=True,
        compaction_priority="HIGH",
        grade="C",
        overall_score=50,
        total_files=100,
        small_files_count=1000,
        total_size_gb=100.0,
        total_snapshots=50,
    )
    session.add(stats)
    session.commit()

    impact = estimate_impact(
        session,
        table.id,
        OperationType.EXPIRE_SNAPSHOTS,
        {"retention_days": 1},
    )

    assert "estimated_snapshots_to_delete" in impact
    assert "warning" in impact


def test_request_auto_expiration(session):
    """Test that stale pending requests are marked expired."""
    table = _seed_table(session, schema_name="test", table_name="expire_table")
    session.commit()

    approval = create_approval_request(
        session,
        _approval_request_data(
            session,
            table,
            OperationType.EXPIRE_SNAPSHOTS,
            operation_params={"retention_days": 1},
        ),
        auto_expire_hours=1,
    )
    session.commit()

    approval.expires_at = datetime.now(UTC) - timedelta(hours=2)
    session.commit()

    expired_count = expire_stale_approval_requests(session)
    assert expired_count == 1

    session.refresh(approval)
    assert approval.status == ApprovalStatus.EXPIRED
