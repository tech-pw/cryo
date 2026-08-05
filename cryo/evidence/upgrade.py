from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass

UPGRADE_EVIDENCE_SCHEMA = "cryo.upgrade-rollback-evidence/v1"


@dataclass(frozen=True, slots=True)
class RenderedMigration:
    direction: str
    sha256: str
    size_bytes: int
    transaction_wrapped: bool


@dataclass(frozen=True, slots=True)
class UpgradeRollbackEvidence:
    schema_version: str
    revision: str
    status: str
    mode: str
    upgrade: RenderedMigration
    rollback: RenderedMigration
    production_qualified: bool
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_upgrade_rollback_evidence(
    *,
    revision: str,
    upgrade_sql: bytes,
    rollback_sql: bytes,
) -> UpgradeRollbackEvidence:
    if not revision.strip():
        raise ValueError("revision must not be empty")
    upgrade = _rendered_migration("upgrade", upgrade_sql)
    rollback = _rendered_migration("rollback", rollback_sql)
    render_verified = (
        upgrade.size_bytes > 0
        and rollback.size_bytes > 0
        and upgrade.transaction_wrapped
        and rollback.transaction_wrapped
    )
    return UpgradeRollbackEvidence(
        schema_version=UPGRADE_EVIDENCE_SCHEMA,
        revision=revision,
        status="repository_render_verified" if render_verified else "incomplete",
        mode="postgresql_offline_sql",
        upgrade=upgrade,
        rollback=rollback,
        production_qualified=False,
        blockers=(
            "requires_production_shaped_upgrade_with_preserved_data",
            "requires_production_shaped_rollback_with_preserved_data",
            "requires_measured_service_availability_and_duration",
        ),
    )


def _rendered_migration(direction: str, sql: bytes) -> RenderedMigration:
    normalized = sql.strip()
    upper = normalized.upper()
    return RenderedMigration(
        direction=direction,
        sha256=hashlib.sha256(normalized).hexdigest(),
        size_bytes=len(normalized),
        transaction_wrapped=upper.startswith(b"BEGIN;") and upper.endswith(b"COMMIT;"),
    )
