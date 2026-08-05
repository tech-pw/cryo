from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import secrets
import struct
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.engine import URL, make_url

MAGIC = b"CRYO-BACKUP-V1\n"
CHUNK_SIZE = 4 * 1024 * 1024
CommandRunner = Callable[[Sequence[str], dict[str, str]], None]


@dataclass(frozen=True)
class BackupManifest:
    schema_version: str
    created_at: str
    backup_file: str
    plaintext_sha256: str
    ciphertext_sha256: str
    encryption: str
    chunk_size: int


@dataclass(frozen=True)
class OffsiteBackupResult:
    manifest: BackupManifest
    backup_uri: str
    manifest_uri: str
    deleted_objects: int


class S3BackupClient(Protocol):
    def upload_file(
        self,
        filename: str,
        bucket: str,
        key: str,
        ExtraArgs: dict[str, Any] | None = None,
    ) -> None: ...

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]: ...

    def delete_objects(self, **kwargs: Any) -> dict[str, Any]: ...


def create_backup(
    database_url: str,
    output: Path,
    key: bytes,
    *,
    runner: CommandRunner | None = None,
    now: datetime | None = None,
) -> BackupManifest:
    """Create a PostgreSQL custom-format dump and encrypt it with chunked AES-256-GCM."""
    _validate_key(key)
    output.parent.mkdir(parents=True, exist_ok=True)
    run = runner or _run_command
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".cryo-backup-",
            suffix=".dump",
            dir=output.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)

        command, env = _postgres_command("pg_dump", database_url)
        run(
            [
                *command,
                "--format=custom",
                "--no-owner",
                "--no-acl",
                f"--file={temporary_path}",
            ],
            env,
        )
        plaintext_sha256 = _encrypt_file(temporary_path, output, key)
        manifest = BackupManifest(
            schema_version="cryo.backup/v1",
            created_at=(now or datetime.now(UTC)).isoformat(),
            backup_file=output.name,
            plaintext_sha256=plaintext_sha256,
            ciphertext_sha256=_sha256(output),
            encryption="AES-256-GCM/chunked",
            chunk_size=CHUNK_SIZE,
        )
        _manifest_path(output).write_text(
            json.dumps(asdict(manifest), indent=2, sort_keys=True) + "\n"
        )
        return manifest
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def restore_backup(
    database_url: str,
    backup: Path,
    key: bytes,
    *,
    force: bool,
    runner: CommandRunner | None = None,
) -> None:
    """Verify, decrypt, and restore a Cryo backup. Restore is always explicitly force-gated."""
    if not force:
        raise ValueError("restore requires force=True")
    _validate_key(key)
    manifest = _load_manifest(backup)
    if manifest.ciphertext_sha256 != _sha256(backup):
        raise ValueError("backup ciphertext checksum does not match its manifest")

    run = runner or _run_command
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".cryo-restore-",
            suffix=".dump",
            dir=backup.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)

        plaintext_sha256 = _decrypt_file(backup, temporary_path, key)
        if plaintext_sha256 != manifest.plaintext_sha256:
            raise ValueError("decrypted backup checksum does not match its manifest")

        command, env = _postgres_command("pg_restore", database_url)
        run(
            [
                *command,
                "--exit-on-error",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-acl",
                str(temporary_path),
            ],
            env,
        )
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def create_offsite_backup(
    database_url: str,
    work_dir: Path,
    key: bytes,
    s3_uri: str,
    *,
    retention_days: int,
    runner: CommandRunner | None = None,
    s3_client: S3BackupClient | None = None,
    now: datetime | None = None,
) -> OffsiteBackupResult:
    if retention_days < 1:
        raise ValueError("backup retention must be at least one day")
    current = now or datetime.now(UTC)
    filename = f"cryo-{current.strftime('%Y%m%dT%H%M%SZ')}.dump.enc"
    backup = work_dir / filename
    try:
        manifest = create_backup(
            database_url,
            backup,
            key,
            runner=runner,
            now=current,
        )
        return upload_backup(
            backup,
            s3_uri,
            retention_days=retention_days,
            client=s3_client,
            now=current,
            manifest=manifest,
        )
    finally:
        backup.unlink(missing_ok=True)
        _manifest_path(backup).unlink(missing_ok=True)


def upload_backup(
    backup: Path,
    s3_uri: str,
    *,
    retention_days: int,
    client: S3BackupClient | None = None,
    now: datetime | None = None,
    manifest: BackupManifest | None = None,
) -> OffsiteBackupResult:
    if retention_days < 1:
        raise ValueError("backup retention must be at least one day")
    loaded_manifest = manifest or _load_manifest(backup)
    if loaded_manifest.ciphertext_sha256 != _sha256(backup):
        raise ValueError("backup ciphertext checksum does not match its manifest")
    bucket, prefix = _parse_s3_uri(s3_uri)
    s3 = client or _default_s3_client()
    backup_key = f"{prefix}{backup.name}"
    manifest_path = _manifest_path(backup)
    manifest_key = f"{prefix}{manifest_path.name}"
    s3.upload_file(
        str(backup),
        bucket,
        backup_key,
        ExtraArgs={
            "Metadata": {
                "cryo-schema": loaded_manifest.schema_version,
                "sha256": loaded_manifest.ciphertext_sha256,
            }
        },
    )
    # The manifest is the commit marker: readers ignore an uploaded envelope until this succeeds.
    s3.upload_file(
        str(manifest_path),
        bucket,
        manifest_key,
        ExtraArgs={"ContentType": "application/json"},
    )
    deleted = _prune_offsite_backups(
        s3,
        bucket=bucket,
        prefix=prefix,
        cutoff=(now or datetime.now(UTC)) - timedelta(days=retention_days),
        protected_keys={backup_key, manifest_key},
    )
    return OffsiteBackupResult(
        manifest=loaded_manifest,
        backup_uri=f"s3://{bucket}/{backup_key}",
        manifest_uri=f"s3://{bucket}/{manifest_key}",
        deleted_objects=deleted,
    )


def load_key(value: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(value.encode())
    except (ValueError, binascii.Error) as exc:
        raise ValueError("backup key must be URL-safe base64") from exc
    _validate_key(key)
    return key


def _encrypt_file(source: Path, destination: Path, key: bytes) -> str:
    aes = AESGCM(key)
    nonce_prefix = secrets.token_bytes(8)
    plaintext_hash = hashlib.sha256()
    with source.open("rb") as source_file, destination.open("wb") as destination_file:
        destination_file.write(MAGIC)
        destination_file.write(nonce_prefix)
        chunk_index = 0
        while chunk := source_file.read(CHUNK_SIZE):
            plaintext_hash.update(chunk)
            nonce = nonce_prefix + chunk_index.to_bytes(4, "big")
            ciphertext = aes.encrypt(nonce, chunk, chunk_index.to_bytes(8, "big"))
            destination_file.write(struct.pack(">I", len(ciphertext)))
            destination_file.write(ciphertext)
            chunk_index += 1
        destination_file.write(struct.pack(">I", 0))
    return plaintext_hash.hexdigest()


def _decrypt_file(source: Path, destination: Path, key: bytes) -> str:
    aes = AESGCM(key)
    plaintext_hash = hashlib.sha256()
    with source.open("rb") as source_file, destination.open("wb") as destination_file:
        if source_file.read(len(MAGIC)) != MAGIC:
            raise ValueError("not a Cryo backup envelope")
        nonce_prefix = source_file.read(8)
        if len(nonce_prefix) != 8:
            raise ValueError("truncated Cryo backup envelope")

        chunk_index = 0
        while True:
            encoded_length = source_file.read(4)
            if len(encoded_length) != 4:
                raise ValueError("truncated Cryo backup chunk length")
            length = struct.unpack(">I", encoded_length)[0]
            if length == 0:
                if source_file.read(1):
                    raise ValueError("unexpected data after Cryo backup terminator")
                break
            ciphertext = source_file.read(length)
            if len(ciphertext) != length:
                raise ValueError("truncated Cryo backup chunk")
            nonce = nonce_prefix + chunk_index.to_bytes(4, "big")
            plaintext = aes.decrypt(nonce, ciphertext, chunk_index.to_bytes(8, "big"))
            destination_file.write(plaintext)
            plaintext_hash.update(plaintext)
            chunk_index += 1
    return plaintext_hash.hexdigest()


def _postgres_command(executable: str, database_url: str) -> tuple[list[str], dict[str, str]]:
    url = make_url(database_url)
    if not url.drivername.startswith("postgresql"):
        raise ValueError("backup and restore require a PostgreSQL database URL")
    password = url.password
    clean_url = URL.create(
        "postgresql",
        username=url.username,
        host=url.host,
        port=url.port,
        database=url.database,
        query=url.query,
    )
    env = os.environ.copy()
    if password is not None:
        env["PGPASSWORD"] = password
    return [executable, f"--dbname={clean_url.render_as_string(hide_password=False)}"], env


def _run_command(command: Sequence[str], env: dict[str, str]) -> None:
    subprocess.run(command, env=env, check=True)


def _default_s3_client() -> S3BackupClient:
    import boto3

    return cast(S3BackupClient, boto3.client("s3"))


def _parse_s3_uri(value: str) -> tuple[str, str]:
    parsed = urlparse(value)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError("offsite backup destination must be an s3:// URI")
    prefix = parsed.path.lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return parsed.netloc, prefix


def _prune_offsite_backups(
    client: S3BackupClient,
    *,
    bucket: str,
    prefix: str,
    cutoff: datetime,
    protected_keys: set[str],
) -> int:
    candidates: list[str] = []
    continuation_token: str | None = None
    while True:
        request: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if continuation_token is not None:
            request["ContinuationToken"] = continuation_token
        response = client.list_objects_v2(**request)
        for item in response.get("Contents", []):
            object_key = item.get("Key")
            last_modified = item.get("LastModified")
            if (
                not isinstance(object_key, str)
                or object_key in protected_keys
                or not _is_cryo_backup_object(object_key, prefix)
                or not isinstance(last_modified, datetime)
            ):
                continue
            modified = (
                last_modified
                if last_modified.tzinfo is not None
                else last_modified.replace(tzinfo=UTC)
            )
            if modified < cutoff:
                candidates.append(object_key)
        if not response.get("IsTruncated"):
            break
        token = response.get("NextContinuationToken")
        if not isinstance(token, str):
            raise RuntimeError("truncated S3 listing did not return a continuation token")
        continuation_token = token

    for offset in range(0, len(candidates), 1000):
        client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": key} for key in candidates[offset : offset + 1000]]},
        )
    return len(candidates)


def _is_cryo_backup_object(key: str, prefix: str) -> bool:
    filename = key.removeprefix(prefix)
    return filename.startswith("cryo-") and (
        filename.endswith(".dump.enc") or filename.endswith(".dump.enc.manifest.json")
    )


def _load_manifest(backup: Path) -> BackupManifest:
    document = json.loads(_manifest_path(backup).read_text())
    manifest = BackupManifest(**document)
    if manifest.schema_version != "cryo.backup/v1":
        raise ValueError(f"unsupported backup schema: {manifest.schema_version}")
    if manifest.backup_file != backup.name:
        raise ValueError("backup filename does not match its manifest")
    return manifest


def _manifest_path(backup: Path) -> Path:
    return backup.with_suffix(f"{backup.suffix}.manifest.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_key(key: bytes) -> None:
    if len(key) != 32:
        raise ValueError("backup key must decode to exactly 32 bytes")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or restore encrypted Cryo PostgreSQL backups."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("create")
    backup_parser.add_argument("--database-url", default=os.getenv("CRYO_DATABASE_URL"))
    backup_parser.add_argument("--output", type=Path, required=True)
    offsite_parser = subparsers.add_parser("create-offsite")
    offsite_parser.add_argument("--database-url", default=os.getenv("CRYO_DATABASE_URL"))
    offsite_parser.add_argument("--work-dir", type=Path, default=Path("/tmp"))
    offsite_parser.add_argument("--s3-uri", required=True)
    offsite_parser.add_argument("--retention-days", type=int, default=30)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--database-url", default=os.getenv("CRYO_DATABASE_URL"))
    restore_parser.add_argument("--backup", type=Path, required=True)
    restore_parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = create_parser().parse_args(argv)
    if not args.database_url:
        raise SystemExit("CRYO_DATABASE_URL or --database-url is required")
    encoded_key = os.getenv("CRYO_BACKUP_KEY")
    if not encoded_key:
        raise SystemExit("CRYO_BACKUP_KEY is required")
    key = load_key(encoded_key)
    if args.command == "create":
        manifest = create_backup(args.database_url, args.output, key)
        print(json.dumps(asdict(manifest), sort_keys=True))
    elif args.command == "create-offsite":
        result = create_offsite_backup(
            args.database_url,
            args.work_dir,
            key,
            args.s3_uri,
            retention_days=args.retention_days,
        )
        print(json.dumps(asdict(result), sort_keys=True))
    else:
        restore_backup(args.database_url, args.backup, key, force=args.force)


if __name__ == "__main__":
    main()
