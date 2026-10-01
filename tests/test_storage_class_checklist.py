"""Storage-class checklist: readable vs restore-required, missing sample stays unknown."""

from __future__ import annotations

import uuid
from typing import Any

import boto3
import pytest
from sqlalchemy.orm import Session

from cryo.db.models import Source, Table
from cryo.scanner.checklist import _check_storage_class, classify_object_storage_class


def _seed_table(session: Session) -> Table:
    source = Source(
        id=uuid.uuid4(),
        name=f"src-{uuid.uuid4()}",
        type="rest",
        config={"s3.region": "ap-south-1"},
    )
    table = Table(
        id=uuid.uuid4(),
        source_id=source.id,
        schema_name="demo",
        table_name="events",
        is_active=True,
    )
    session.add(source)
    session.add(table)
    session.commit()
    return table


class _HeadClient:
    def __init__(self, heads: dict[str, dict[str, Any]]) -> None:
        self.heads = heads
        self.calls: list[tuple[str, str]] = []

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.calls.append((Bucket, Key))
        return self.heads[Key]


def _patch_s3(monkeypatch: pytest.MonkeyPatch, heads: dict[str, dict[str, Any]]) -> _HeadClient:
    client = _HeadClient(heads)
    monkeypatch.setattr(boto3, "client", lambda *_args, **_kwargs: client)
    return client


@pytest.mark.parametrize(
    ("storage_class", "archive_status", "expected"),
    [
        ("STANDARD", None, "readable"),
        (None, None, "readable"),
        ("INTELLIGENT_TIERING", None, "readable"),
        ("INTELLIGENT_TIERING", "", "readable"),
        ("INTELLIGENT_TIERING", "ARCHIVE_ACCESS", "restore_required"),
        ("INTELLIGENT_TIERING", "DEEP_ARCHIVE_ACCESS", "restore_required"),
        ("STANDARD_IA", None, "readable"),
        ("ONEZONE_IA", None, "readable"),
        ("GLACIER_IR", None, "readable"),
        ("standard_ia", None, "readable"),
        ("glacier_ir", None, "readable"),
        ("GLACIER", None, "restore_required"),
        ("DEEP_ARCHIVE", None, "restore_required"),
        ("REDUCED_REDUNDANCY", None, "unknown"),
    ],
)
def test_classify_object_storage_class(
    storage_class: str | None,
    archive_status: str | None,
    expected: str,
) -> None:
    assert classify_object_storage_class(storage_class, archive_status) == expected


def test_missing_sample_is_unknown_not_green_or_red(session: Session) -> None:
    table = _seed_table(session)
    for sample in (None, []):
        item = _check_storage_class(session, table.id, sample_paths=sample)
        assert item.status == "unknown"
        assert item.status not in ("green", "red")


@pytest.mark.parametrize(
    ("head", "status"),
    [
        ({}, "green"),
        ({"StorageClass": "STANDARD"}, "green"),
        ({"StorageClass": "STANDARD_IA"}, "green"),
        ({"StorageClass": "ONEZONE_IA"}, "green"),
        ({"StorageClass": "GLACIER_IR"}, "green"),
        ({"StorageClass": "INTELLIGENT_TIERING"}, "green"),
        ({"StorageClass": "GLACIER"}, "red"),
        ({"StorageClass": "DEEP_ARCHIVE"}, "red"),
        (
            {"StorageClass": "INTELLIGENT_TIERING", "ArchiveStatus": "ARCHIVE_ACCESS"},
            "red",
        ),
        (
            {"StorageClass": "INTELLIGENT_TIERING", "ArchiveStatus": "DEEP_ARCHIVE_ACCESS"},
            "red",
        ),
        ({"StorageClass": "REDUCED_REDUNDANCY"}, "unknown"),
    ],
)
def test_head_object_class_sets_checklist_status(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    head: dict[str, Any],
    status: str,
) -> None:
    table = _seed_table(session)
    key = "warehouse/events/data.parquet"
    client = _patch_s3(monkeypatch, {key: head})

    item = _check_storage_class(session, table.id, sample_paths=[f"s3://lake/{key}"])

    assert item.status == status
    assert client.calls == [("lake", key)]
    if head.get("StorageClass") in ("STANDARD_IA", "GLACIER_IR", "ONEZONE_IA"):
        assert item.status != "red"


def test_standard_ia_does_not_mask_a_glacier_object(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _seed_table(session)
    _patch_s3(
        monkeypatch,
        {
            "ia.parquet": {"StorageClass": "STANDARD_IA"},
            "cold.parquet": {"StorageClass": "GLACIER"},
        },
    )
    item = _check_storage_class(
        session,
        table.id,
        sample_paths=["s3://lake/ia.parquet", "s3a://lake/cold.parquet"],
    )
    assert item.status == "red"
    assert "GLACIER" in item.reason
    assert "STANDARD_IA" not in item.reason
