from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from cryo.operations.backup import create_backup, create_offsite_backup, load_key, restore_backup

KEY = b"k" * 32
DATABASE_URL = "postgresql+psycopg://cryo:secret@postgres:5432/cryo"


def test_backup_and_restore_round_trip_without_exposing_password(tmp_path: Path) -> None:
    backup = tmp_path / "cryo.dump.enc"
    commands: list[tuple[list[str], dict[str, str]]] = []

    def runner(command, env) -> None:
        command = list(command)
        commands.append((command, env))
        if command[0] == "pg_dump":
            output = Path(
                next(arg.split("=", 1)[1] for arg in command if arg.startswith("--file="))
            )
            output.write_bytes((b"durable-intents-and-audit\n" * 400_000) + b"end")

    manifest = create_backup(DATABASE_URL, backup, KEY, runner=runner)
    restore_backup(DATABASE_URL, backup, KEY, force=True, runner=runner)

    assert backup.exists()
    assert backup.with_suffix(".enc.manifest.json").exists()
    assert manifest.encryption == "AES-256-GCM/chunked"
    assert [command[0][0] for command in commands] == ["pg_dump", "pg_restore"]
    for command, env in commands:
        assert all("secret" not in argument for argument in command)
        assert env["PGPASSWORD"] == "secret"


def test_restore_rejects_tampered_ciphertext_before_pg_restore(tmp_path: Path) -> None:
    backup = tmp_path / "cryo.dump.enc"

    def dump_runner(command, env) -> None:
        output = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--file=")))
        output.write_bytes(b"valid database dump")

    create_backup(DATABASE_URL, backup, KEY, runner=dump_runner)
    backup.write_bytes(backup.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="ciphertext checksum"):
        restore_backup(
            DATABASE_URL,
            backup,
            KEY,
            force=True,
            runner=lambda command, env: pytest.fail("pg_restore must not run"),
        )


def test_restore_requires_explicit_force(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="force"):
        restore_backup(DATABASE_URL, tmp_path / "missing", KEY, force=False)


def test_load_key_requires_urlsafe_base64_32_byte_key() -> None:
    assert load_key(base64.urlsafe_b64encode(KEY).decode()) == KEY
    with pytest.raises(ValueError, match="32 bytes"):
        load_key(base64.urlsafe_b64encode(b"short").decode())


def test_offsite_backup_uploads_manifest_last_and_prunes_only_old_cryo_objects(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    client = _FakeS3Client(
        objects=[
            {
                "Key": "backups/cryo-20260601T000000Z.dump.enc",
                "LastModified": now - timedelta(days=60),
            },
            {
                "Key": "backups/cryo-20260601T000000Z.dump.enc.manifest.json",
                "LastModified": now - timedelta(days=60),
            },
            {
                "Key": "backups/keep-me.txt",
                "LastModified": now - timedelta(days=60),
            },
        ]
    )

    def dump_runner(command, env) -> None:
        output = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--file=")))
        output.write_bytes(b"durable backup")

    result = create_offsite_backup(
        DATABASE_URL,
        tmp_path,
        KEY,
        "s3://recovery-bucket/backups",
        retention_days=30,
        runner=dump_runner,
        s3_client=client,
        now=now,
    )

    assert result.backup_uri == "s3://recovery-bucket/backups/cryo-20260804T120000Z.dump.enc"
    assert result.deleted_objects == 2
    assert [upload[2] for upload in client.uploads] == [
        "backups/cryo-20260804T120000Z.dump.enc",
        "backups/cryo-20260804T120000Z.dump.enc.manifest.json",
    ]
    assert client.deleted == {
        "backups/cryo-20260601T000000Z.dump.enc",
        "backups/cryo-20260601T000000Z.dump.enc.manifest.json",
    }
    assert not list(tmp_path.iterdir())


def test_offsite_backup_requires_s3_uri_and_positive_retention(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one day"):
        create_offsite_backup(
            DATABASE_URL,
            tmp_path,
            KEY,
            "s3://bucket/backups",
            retention_days=0,
        )


class _FakeS3Client:
    def __init__(self, *, objects: list[dict[str, Any]]) -> None:
        self.objects = objects
        self.uploads: list[tuple[str, str, str, dict[str, Any] | None]] = []
        self.deleted: set[str] = set()

    def upload_file(
        self,
        filename: str,
        bucket: str,
        key: str,
        ExtraArgs: dict[str, Any] | None = None,
    ) -> None:
        self.uploads.append((filename, bucket, key, ExtraArgs))

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        return {"Contents": self.objects, "IsTruncated": False}

    def delete_objects(self, **kwargs: Any) -> dict[str, Any]:
        self.deleted.update(item["Key"] for item in kwargs["Delete"]["Objects"])
        return {}
