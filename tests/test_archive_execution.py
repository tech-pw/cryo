"""Archive / restore execution: in-place re-classing, permission probes, jobs and routes."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.api.deps import get_tenant_session
from cryo.api.routes_archive import router
from cryo.db.models import (
    ArchiveJob,
    AuditLog,
    PartitionStorageClass,
    Source,
    Table,
    TablePartition,
)
from cryo.planner.planner import _candidate_partitions
from cryo.tiering import archive_jobs
from cryo.tiering.executor import (
    MIN_OBJECT_BYTES,
    ObjectRef,
    bucket_guardrails,
    check_permissions,
    iam_policy,
    transition_objects,
)
from tests.auth_helpers import OPERATOR_HEADERS, VIEWER_HEADERS, ensure_test_jwt_secret

BIG = 4 * 1024 * 1024


class _ClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(self, objects: dict[str, dict[str, Any]] | None = None) -> None:
        self.objects = objects or {}
        self.copies: list[dict[str, Any]] = []
        self.deny_put = False
        self.bucket: dict[str, Any] = {
            "versioning": {},
            "lifecycle": _ClientError("NoSuchLifecycleConfiguration"),
            "object_lock": _ClientError("ObjectLockConfigurationNotFoundError"),
            "replication": _ClientError("ReplicationConfigurationNotFoundError"),
        }

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        if Key not in self.objects:
            raise _ClientError("404")
        obj = self.objects[Key]
        return {
            "ContentLength": obj["size"],
            "StorageClass": obj.get("class"),
            "ETag": '"e"',
            **obj.get("extra", {}),
        }

    def copy_object(self, **kwargs: Any) -> dict[str, Any]:
        if self.deny_put:
            raise _ClientError("AccessDenied")
        key = kwargs["Key"]
        current = self.objects[key].get("class") or "STANDARD"
        if kwargs["StorageClass"] == current and "ServerSideEncryption" not in kwargs:
            raise _ClientError("InvalidRequest")
        self.copies.append(kwargs)
        self.objects[key]["class"] = (
            None if kwargs["StorageClass"] == "STANDARD" else kwargs["StorageClass"]
        )
        return {}

    def _bucket_read(self, name: str) -> dict[str, Any]:
        value = self.bucket[name]
        if isinstance(value, Exception):
            raise value
        return value

    def get_bucket_versioning(self, *, Bucket: str) -> dict[str, Any]:
        return self._bucket_read("versioning")

    def get_bucket_lifecycle_configuration(self, *, Bucket: str) -> dict[str, Any]:
        return self._bucket_read("lifecycle")

    def get_object_lock_configuration(self, *, Bucket: str) -> dict[str, Any]:
        return self._bucket_read("object_lock")

    def get_bucket_replication(self, *, Bucket: str) -> dict[str, Any]:
        return self._bucket_read("replication")


def test_transition_moves_in_place_and_skips_what_it_should() -> None:
    s3 = FakeS3(
        {
            "a": {"size": BIG},
            "small": {"size": MIN_OBJECT_BYTES - 1},
            "done": {"size": BIG, "class": "GLACIER_IR"},
            "offline": {"size": BIG, "class": "DEEP_ARCHIVE"},
        }
    )
    refs = [ObjectRef("b", key) for key in ("a", "small", "done", "offline", "gone")]
    result = transition_objects(s3, refs, "GLACIER_IR")
    assert (result.moved, result.skipped, result.failed) == (1, 3, 1)
    assert result.bytes_moved == BIG
    assert s3.objects["a"]["class"] == "GLACIER_IR"
    copy = s3.copies[0]
    assert copy["CopySource"] == {"Bucket": "b", "Key": "a"}
    assert copy["MetadataDirective"] == "COPY"
    assert copy["CopySourceIfMatch"] == '"e"'
    assert "needs an S3 restore" in result.errors[0]


def test_transition_refuses_offline_targets() -> None:
    with pytest.raises(ValueError):
        transition_objects(FakeS3(), [ObjectRef("b", "a")], "DEEP_ARCHIVE")


def test_restore_moves_small_files_too() -> None:
    s3 = FakeS3({"small": {"size": 10, "class": "STANDARD_IA"}})
    result = transition_objects(s3, [ObjectRef("b", "small")], "STANDARD")
    assert result.moved == 1
    assert s3.objects["small"]["class"] is None


def test_permission_probe_never_changes_the_object() -> None:
    s3 = FakeS3({"k": {"size": BIG, "extra": {"ServerSideEncryption": "aws:kms"}}})
    checks = {c.action: c.status for c in check_permissions(s3, ObjectRef("b", "k"))}
    assert checks == {"s3:GetObject": "ok", "s3:PutObject": "ok"}
    assert s3.copies == []

    s3.deny_put = True
    checks = {c.action: c.status for c in check_permissions(s3, ObjectRef("b", "k"))}
    assert checks["s3:PutObject"] == "missing"


def test_versioned_bucket_without_noncurrent_expiry_blocks() -> None:
    s3 = FakeS3()
    s3.bucket["versioning"] = {"Status": "Enabled"}
    guardrails, checks = bucket_guardrails(s3, "b")
    status = {g.name: g.status for g in guardrails}
    assert status == {"versioning": "block", "object_lock": "pass", "replication": "pass"}
    assert all(c.status == "ok" for c in checks)

    s3.bucket["lifecycle"] = {
        "Rules": [{"Status": "Enabled", "NoncurrentVersionExpiration": {"NoncurrentDays": 7}}]
    }
    guardrails, _ = bucket_guardrails(s3, "b")
    assert {g.name: g.status for g in guardrails}["versioning"] == "pass"


def test_iam_policy_limits_put_to_instant_classes() -> None:
    policy = json.loads(iam_policy("lake"))
    put = next(s for s in policy["Statement"] if s["Action"] == "s3:PutObject")
    classes = put["Condition"]["StringEquals"]["s3:x-amz-storage-class"]
    assert "DEEP_ARCHIVE" not in classes and "GLACIER" not in classes
    assert put["Resource"] == "arn:aws:s3:::lake/*"


def test_planner_skips_archived_partitions() -> None:
    notes: list[str] = []
    rows = [
        {
            "partition_value": "a",
            "partition_values": {},
            "small_files_in_partition_count": 50,
            "storage_class": "GLACIER_IR",
        },
        {
            "partition_value": "b",
            "partition_values": {},
            "small_files_in_partition_count": 50,
            "storage_class": "INTELLIGENT_TIERING",
        },
    ]
    candidates = _candidate_partitions([], rows, 1, set(), notes)
    assert [c.value for c in candidates] == ["b"]
    assert any("archived partition a" in note and "Restore it first" in note for note in notes)


class _Lock:
    def __init__(self, free: bool = True) -> None:
        self.free = free
        self.released = False

    def acquire_table_lock(self, *_args: Any) -> bool:
        return self.free

    def renew_table_lock(self, *_args: Any) -> bool:
        return True

    def release_table_lock(self, *_args: Any) -> None:
        self.released = True


def _seed(session: Session) -> Table:
    source = Source(
        id=uuid.uuid4(),
        name=f"src-{uuid.uuid4()}",
        type="rest",
        config={"s3.region": "us-east-1", "s3.secret-access-key": "never-returned"},
    )
    table = Table(
        id=uuid.uuid4(),
        source_id=source.id,
        schema_name="sales",
        table_name="orders",
        location="s3://lake/sales/orders",
        is_active=True,
    )
    partition = TablePartition(
        table_id=table.id,
        partition_values={"dt": "2024-01-01"},
        partition_fields=[],
        partition_value="2024-01-01",
        partition_key="dt=2024-01-01",
        partition_size_gb=2.0,
        files_per_partition=4,
        small_files_in_partition_count=0,
        scan_at=datetime(2026, 10, 1, tzinfo=UTC),
        raw={
            "sample_file_path": "s3://lake/sales/orders/data/f1.parquet",
            "last_write_at": "2024-01-02T00:00:00+00:00",
        },
    )
    session.add_all([source, table, partition])
    session.commit()
    return table


def _fake_iceberg(paths: list[str]) -> Any:
    return SimpleNamespace(paths=paths)


@pytest.fixture()
def fake_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    import cryo.scanner.analyze as analyze

    monkeypatch.setattr(analyze, "partition_fields", lambda _table: [])
    monkeypatch.setattr(
        analyze,
        "iter_data_files",
        lambda table: [
            SimpleNamespace(file_path=path, file_size_in_bytes=BIG, content=0)
            for path in table.paths
        ],
    )
    monkeypatch.setattr(analyze, "_is_delete_file", lambda _df: False)
    monkeypatch.setattr(analyze, "data_file_partition_values", lambda _df, _f: {"dt": "2024-01-01"})


def test_run_job_archives_records_class_and_audits(session: Session, fake_scan: None) -> None:
    table = _seed(session)
    job = ArchiveJob(
        table_id=table.id,
        action="archive",
        target_class="GLACIER_IR",
        partition_keys=["dt=2024-01-01"],
        actor="op",
        reason="cold since 2024",
        errors=[],
    )
    session.add(job)
    session.commit()
    s3 = FakeS3({"sales/orders/data/f1.parquet": {"size": BIG}})
    lock = _Lock()
    archive_jobs.run_job(
        job.id,
        lambda: session,
        lock_store_factory=lambda: lock,
        client_factory=lambda _cfg: s3,
        table_loader=lambda *_a: _fake_iceberg(["s3://lake/sales/orders/data/f1.parquet"]),
    )
    session.expire_all()
    job = session.get(ArchiveJob, job.id)
    assert job.status == "succeeded" and job.objects_moved == 1
    state = session.get(PartitionStorageClass, (table.id, "dt=2024-01-01"))
    assert state is not None and state.storage_class == "GLACIER_IR"
    audit = session.scalars(select(AuditLog).where(AuditLog.action == "archive.archive")).one()
    assert "cold since 2024" in audit.detail
    assert lock.released


def test_run_job_refuses_while_maintenance_holds_the_lock(session: Session) -> None:
    table = _seed(session)
    job = ArchiveJob(
        table_id=table.id,
        action="archive",
        target_class="STANDARD_IA",
        partition_keys=["dt=2024-01-01"],
        actor="op",
        reason="test",
        errors=[],
    )
    session.add(job)
    session.commit()
    archive_jobs.run_job(
        job.id,
        lambda: session,
        lock_store_factory=lambda: _Lock(free=False),
        client_factory=lambda _cfg: pytest.fail("must not touch S3"),
        table_loader=lambda *_a: pytest.fail("must not load the table"),
    )
    session.expire_all()
    job = session.get(ArchiveJob, job.id)
    assert job.status == "failed"
    assert "Maintenance is running" in job.errors[0]


def _client(session: Session) -> TestClient:
    ensure_test_jwt_secret()
    app = FastAPI()
    app.include_router(router)

    def override_session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_tenant_session] = override_session
    return TestClient(app)


def _selection(**extra: Any) -> dict[str, Any]:
    return {
        "action": "archive",
        "target_class": "INTELLIGENT_TIERING",
        "partition_keys": ["dt=2024-01-01"],
        **extra,
    }


def test_preflight_lists_missing_permissions_and_the_policy(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _seed(session)
    s3 = FakeS3({"sales/orders/data/f1.parquet": {"size": BIG}})
    s3.deny_put = True
    monkeypatch.setattr(archive_jobs, "s3_client_for_source", lambda _cfg: s3)
    response = _client(session).post(
        f"/api/v1/tables/{table.id}/tiering/preflight", headers=OPERATOR_HEADERS, json=_selection()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["missing_permissions"] == ["s3:PutObject"]
    assert body["can_apply"] is False
    assert "s3:x-amz-storage-class" in body["iam_policy"]
    assert body["partitions_selected"] == 1 and body["object_estimate"] == 4
    assert "never-returned" not in response.text
    assert body["blockers"] == []

    applied = _client(session).post(
        f"/api/v1/tables/{table.id}/tiering/apply",
        headers=OPERATOR_HEADERS,
        json=_selection(reason="archive cold days"),
    )
    assert applied.status_code == 422
    assert any("s3:PutObject" in b for b in applied.json()["detail"]["blockers"])


def test_apply_starts_a_job_and_viewer_cannot(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _seed(session)
    s3 = FakeS3({"sales/orders/data/f1.parquet": {"size": BIG}})
    monkeypatch.setattr(archive_jobs, "s3_client_for_source", lambda _cfg: s3)
    submitted: list[uuid.UUID] = []
    monkeypatch.setattr(archive_jobs, "submit", lambda job_id, _factory: submitted.append(job_id))
    client = _client(session)
    url = f"/api/v1/tables/{table.id}/tiering/apply"

    viewer = client.post(url, headers=VIEWER_HEADERS, json=_selection(reason="x" * 5))
    assert viewer.status_code == 403
    assert client.post(url, headers=OPERATOR_HEADERS, json=_selection()).status_code == 422

    response = client.post(url, headers=OPERATOR_HEADERS, json=_selection(reason="cold data"))
    assert response.status_code == 202, response.text
    assert response.json()["target_class"] == "INTELLIGENT_TIERING"
    assert len(submitted) == 1
    assert s3.copies == []

    again = client.post(url, headers=OPERATOR_HEADERS, json=_selection(reason="cold data"))
    assert again.status_code == 409

    jobs = client.get(f"/api/v1/tables/{table.id}/tiering/jobs", headers=VIEWER_HEADERS)
    assert jobs.status_code == 200 and len(jobs.json()["items"]) == 1


def test_glacier_ir_without_read_evidence_needs_confirmation(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _seed(session)
    s3 = FakeS3({"sales/orders/data/f1.parquet": {"size": BIG}})
    monkeypatch.setattr(archive_jobs, "s3_client_for_source", lambda _cfg: s3)
    monkeypatch.setattr(archive_jobs, "submit", lambda *_a: None)
    client = _client(session)
    url = f"/api/v1/tables/{table.id}/tiering/apply"
    body = _selection(target_class="GLACIER_IR", reason="cold data")
    assert client.post(url, headers=OPERATOR_HEADERS, json=body).status_code == 409
    confirmed = client.post(url, headers=OPERATOR_HEADERS, json={**body, "confirm": True})
    assert confirmed.status_code == 202, confirmed.text


def test_partition_without_a_write_signal_stays_in_standard(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _seed(session)
    partition = session.scalars(select(TablePartition)).one()
    partition.raw = {"sample_file_path": "s3://lake/sales/orders/data/f1.parquet"}
    session.commit()
    monkeypatch.setattr(archive_jobs, "s3_client_for_source", lambda _cfg: FakeS3())
    response = _client(session).post(
        f"/api/v1/tables/{table.id}/tiering/preflight", headers=OPERATOR_HEADERS, json=_selection()
    )
    body = response.json()
    assert body["can_apply"] is False
    assert any("stay in Standard" in blocker for blocker in body["blockers"])


def test_offline_target_is_refused(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    table = _seed(session)
    monkeypatch.setattr(archive_jobs, "s3_client_for_source", lambda _cfg: FakeS3())
    response = _client(session).post(
        f"/api/v1/tables/{table.id}/tiering/preflight",
        headers=OPERATOR_HEADERS,
        json=_selection(target_class="DEEP_ARCHIVE"),
    )
    assert response.status_code == 422


def test_partition_list_filters_by_storage_state(session: Session) -> None:
    table = _seed(session)
    client = _client(session)
    url = f"/api/v1/tables/{table.id}/tiering/partitions"

    standard = client.get(url, params={"state": "standard"}, headers=OPERATOR_HEADERS).json()
    assert [row["partition_key"] for row in standard["items"]] == ["dt=2024-01-01"]
    empty = client.get(url, params={"state": "archived"}, headers=OPERATOR_HEADERS).json()
    assert empty["total"] == 0

    session.add(
        PartitionStorageClass(
            table_id=table.id, partition_key="dt=2024-01-01", storage_class="STANDARD_IA", bytes=BIG
        )
    )
    session.commit()
    archived = client.get(url, params={"state": "archived"}, headers=OPERATOR_HEADERS).json()
    assert [row["current_class"] for row in archived["items"]] == ["STANDARD_IA"]
    remaining = client.get(url, params={"state": "standard"}, headers=OPERATOR_HEADERS).json()
    assert remaining["total"] == 0
