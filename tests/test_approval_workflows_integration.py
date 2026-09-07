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


class TestEnsurePendingApprovalRequests:
    """2026-09-07 fix: the governor deferred jobs needing approval but never created the
    ApprovalRequest an operator would actually see -- confirmed live, 931 tables sat
    deferred for hours with zero ApprovalRequest rows and no way for anyone to notice short
    of reading governor logs. ensure_pending_approval_requests is the fix: called at the
    exact point the governor defers for this reason, it surfaces the block into the
    existing manual approval workflow (get_pending_approvals / approve_request) instead of
    only logging it."""

    def test_creates_a_pending_request_for_a_destructive_op_with_none_existing(
        self, session
    ) -> None:
        from cryo.workflows.approvals import ensure_pending_approval_requests

        table = _seed_table(session, schema_name="test", table_name="needs_approval")
        session.commit()
        plan_operations = [{"type": OperationType.EXPIRE_SNAPSHOTS.value, "params": {"retention_days": 3}}]

        created = ensure_pending_approval_requests(session, table.id, plan_operations)

        assert len(created) == 1
        assert created[0].operation_type == OperationType.EXPIRE_SNAPSHOTS.value
        assert created[0].status == ApprovalStatus.PENDING
        pending = get_pending_approvals(session, table_id=table.id)
        assert len(pending) == 1

    def test_does_not_duplicate_an_already_pending_request(self, session) -> None:
        """The idempotency this needs to be safe called every wave a job stays deferred --
        must not spam a new request each time the governor re-evaluates the same table."""
        from cryo.workflows.approvals import ensure_pending_approval_requests

        table = _seed_table(session, schema_name="test", table_name="already_pending")
        session.commit()
        plan_operations = [{"type": OperationType.EXPIRE_SNAPSHOTS.value, "params": {}}]

        first = ensure_pending_approval_requests(session, table.id, plan_operations)
        second = ensure_pending_approval_requests(session, table.id, plan_operations)

        assert len(first) == 1
        assert len(second) == 0
        pending = get_pending_approvals(session, table_id=table.id)
        assert len(pending) == 1

    def test_ignores_operations_that_do_not_require_approval(self, session) -> None:
        from cryo.workflows.approvals import ensure_pending_approval_requests

        table = _seed_table(session, schema_name="test", table_name="non_destructive")
        session.commit()
        plan_operations = [{"type": OperationType.OPTIMIZE.value, "params": {}}]

        created = ensure_pending_approval_requests(session, table.id, plan_operations)

        assert created == []
        assert get_pending_approvals(session, table_id=table.id) == []

    def test_creates_one_request_per_distinct_destructive_operation_in_the_plan(
        self, session
    ) -> None:
        from cryo.workflows.approvals import ensure_pending_approval_requests

        table = _seed_table(session, schema_name="test", table_name="multi_op")
        session.commit()
        plan_operations = [
            {"type": OperationType.EXPIRE_SNAPSHOTS.value, "params": {}},
            {"type": OperationType.REMOVE_ORPHAN_FILES.value, "params": {}},
            {"type": OperationType.OPTIMIZE.value, "params": {}},
        ]

        created = ensure_pending_approval_requests(session, table.id, plan_operations)

        assert {r.operation_type for r in created} == {
            OperationType.EXPIRE_SNAPSHOTS.value,
            OperationType.REMOVE_ORPHAN_FILES.value,
        }

    def test_returns_empty_for_no_plan_operations(self, session) -> None:
        from cryo.workflows.approvals import ensure_pending_approval_requests

        table = _seed_table(session, schema_name="test", table_name="no_plan")
        session.commit()

        assert ensure_pending_approval_requests(session, table.id, None) == []
        assert ensure_pending_approval_requests(session, table.id, []) == []


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


def test_job_requires_approval_parameter_gated(session):
    """Test parameter-aware gating for individual jobs."""
    table = _seed_table(session, schema_name="test", table_name="param_gate_table")
    session.commit()

    # Create approved request with conservative parameters (e.g. retention_days = 14)
    approval = create_approval_request(
        session,
        _approval_request_data(
            session,
            table,
            OperationType.EXPIRE_SNAPSHOTS,
            operation_params={"retention_days": 14, "retain_last": 5},
        ),
    )
    approve_request(session, approval.id, approved_by="approver@example.com")
    session.commit()

    # 1. Safe run: retention_days = 30 (greater than 14), retain_last = 10 (greater than 5)
    plan_safe = [
        {
            "type": OperationType.EXPIRE_SNAPSHOTS.value,
            "params": {"retention_days": 30, "retain_last": 10},
        }
    ]
    assert job_requires_approval(session, table.id, plan_safe) is False

    # 2. Unsafe retention: retention_days = 5 (less than 14)
    plan_unsafe_retention = [
        {
            "type": OperationType.EXPIRE_SNAPSHOTS.value,
            "params": {"retention_days": 5, "retain_last": 5},
        }
    ]
    assert job_requires_approval(session, table.id, plan_unsafe_retention) is True

    # 3. Unsafe retain_last: retain_last = 2 (less than 5)
    plan_unsafe_retain = [
        {
            "type": OperationType.EXPIRE_SNAPSHOTS.value,
            "params": {"retention_days": 14, "retain_last": 2},
        }
    ]
    assert job_requires_approval(session, table.id, plan_unsafe_retain) is True


def test_batch_job_requires_approval_parameter_gated(session):
    """Test parameter-aware gating for batch governor checks."""
    from cryo.governor.prefetch import batch_job_requires_approval, prefetch_approved_operation_keys

    table = _seed_table(session, schema_name="test", table_name="param_batch_table")
    session.commit()

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

    approved_keys = prefetch_approved_operation_keys(session, {table.id})

    # 1. Safe batch: requested retention_days = 7
    plan_safe = [{"type": OperationType.EXPIRE_SNAPSHOTS.value, "params": {"retention_days": 7}}]
    assert batch_job_requires_approval(table.id, plan_safe, approved_keys) is False

    # 2. Unsafe batch: requested retention_days = 3
    plan_unsafe = [{"type": OperationType.EXPIRE_SNAPSHOTS.value, "params": {"retention_days": 3}}]
    assert batch_job_requires_approval(table.id, plan_unsafe, approved_keys) is True


def test_standing_grant_with_null_expiry_is_matchable(session):
    """Regression test for the live prod bug found 2026-08-19: prefetch_approved_operation_keys
    used to require expires_at IS NOT NULL, which made every standing-grant approval
    (expires_at=NULL, created outside create_approval_request's normal 72h-expiring path --
    e.g. a manual fleet-wide pre-approval sweep) permanently unmatchable. Verified live: of
    10,460 status='approved' rows in prod, 0 satisfied the old query's filters -- every one had
    expires_at IS NULL. NULL means "no expiry constraint", not "always excluded"."""
    from cryo.governor.prefetch import batch_job_requires_approval, prefetch_approved_operation_keys
    from cryo.workflows.approvals import ApprovalRequest

    table = _seed_table(session, schema_name="test", table_name="standing_grant_table")
    session.commit()

    session.add(
        ApprovalRequest(
            id=uuid4(),
            table_id=table.id,
            operation_type=OperationType.EXPIRE_SNAPSHOTS.value,
            requested_by="fleet-sweep",
            approved_by="ops",
            status=ApprovalStatus.APPROVED,
            operation_params={},
            approved_at=datetime.now(UTC),
            expires_at=None,
        )
    )
    session.commit()

    approved_keys = prefetch_approved_operation_keys(session, {table.id})
    assert (table.id, OperationType.EXPIRE_SNAPSHOTS.value) in approved_keys

    plan = [{"type": OperationType.EXPIRE_SNAPSHOTS.value, "params": {}}]
    assert batch_job_requires_approval(table.id, plan, approved_keys) is False


def test_already_executed_standing_grant_still_matches_next_run(session):
    """Regression test for the live prod bug found 2026-08-19: prefetch_approved_operation_keys
    used to exclude every row with executed_at IS NOT NULL, silently turning every approval
    into a single-use grant the moment the governor's own JobWatcher stamped it as executed.
    expire_snapshots/remove_orphan_files are meant to run repeatedly forever -- an approval for
    them must keep authorizing future runs after the first successful execution, not just the
    first one. Verified live: 8,547 of 10,460 approved rows in prod already had a non-NULL
    executed_at from a prior run and were being silently excluded by this filter."""
    from cryo.governor.prefetch import batch_job_requires_approval, prefetch_approved_operation_keys
    from cryo.workflows.approvals import ApprovalRequest

    table = _seed_table(session, schema_name="test", table_name="already_executed_table")
    session.commit()

    session.add(
        ApprovalRequest(
            id=uuid4(),
            table_id=table.id,
            operation_type=OperationType.REMOVE_ORPHAN_FILES.value,
            requested_by="fleet-sweep",
            approved_by="ops",
            status=ApprovalStatus.APPROVED,
            operation_params={},
            approved_at=datetime.now(UTC) - timedelta(days=10),
            expires_at=None,
            executed_at=datetime.now(UTC) - timedelta(days=5),
            execution_job_id=uuid4(),
        )
    )
    session.commit()

    approved_keys = prefetch_approved_operation_keys(session, {table.id})
    plan = [{"type": OperationType.REMOVE_ORPHAN_FILES.value, "params": {}}]
    assert batch_job_requires_approval(table.id, plan, approved_keys) is False


def test_expired_standing_grant_style_approval_is_excluded(session):
    """A real expiry (expires_at in the past) must still exclude the row -- the NULL-handling
    fix must not accidentally admit genuinely-expired approvals too."""
    from cryo.governor.prefetch import batch_job_requires_approval, prefetch_approved_operation_keys
    from cryo.workflows.approvals import ApprovalRequest

    table = _seed_table(session, schema_name="test", table_name="expired_approval_table")
    session.commit()

    session.add(
        ApprovalRequest(
            id=uuid4(),
            table_id=table.id,
            operation_type=OperationType.EXPIRE_SNAPSHOTS.value,
            requested_by="user@example.com",
            approved_by="ops",
            status=ApprovalStatus.APPROVED,
            operation_params={},
            approved_at=datetime.now(UTC) - timedelta(days=5),
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    session.commit()

    approved_keys = prefetch_approved_operation_keys(session, {table.id})
    plan = [{"type": OperationType.EXPIRE_SNAPSHOTS.value, "params": {}}]
    assert batch_job_requires_approval(table.id, plan, approved_keys) is True


def test_single_use_approval_retirement(session):
    """Test that a successful destructive operation retires its approved request."""
    from cryo.db.repositories.job_store import SQLJobStore
    from cryo.worker.worker import AuditRecord
    from cryo.workflows.approvals import ApprovalRequest

    table = _seed_table(session, schema_name="test", table_name="single_use_table")
    session.commit()

    # Create approved request
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

    # Verify initially approved and not executed
    assert approval.status == ApprovalStatus.APPROVED
    assert approval.executed_at is None

    # Record a successful audit log for EXPIRE_SNAPSHOTS
    job_id = uuid4()
    record = AuditRecord(
        job_id=str(job_id),
        table_id=str(table.id),
        action=OperationType.EXPIRE_SNAPSHOTS,
        status="OK",
        engine="trino",
    )

    job_store = SQLJobStore(lambda: session)
    job_store.record_audit(record)

    # Expire session state and reload approval request to check it's marked executed
    session.expire_all()
    updated_approval = session.get(ApprovalRequest, approval.id)
    assert updated_approval is not None
    assert updated_approval.executed_at is not None
    assert updated_approval.execution_job_id == job_id
