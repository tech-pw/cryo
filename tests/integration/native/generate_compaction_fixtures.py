#!/usr/bin/env python3
"""Seed COW + MOR compaction fixtures on the local REST catalog + MinIO."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.integration.native.compaction_fixtures import ensure_compaction_fixtures

SEED_BINARY_ENV = "CRYO_SEED_NATIVE_FIXTURES_BINARY"


def _resolve_seed_binary() -> Path:
    configured = os.getenv(SEED_BINARY_ENV)
    if configured:
        return Path(configured)
    candidate = REPO_ROOT / "engine-go" / "bin" / "seed-native-fixtures"
    return candidate


def _seed_mor_fixtures() -> None:
    binary = _resolve_seed_binary()
    if not binary.is_file():
        raise FileNotFoundError(
            f"MOR fixture seeder not found at {binary}; run `make build-seed-native-fixtures`"
        )
    subprocess.run([str(binary)], check=True, env=os.environ.copy())


def main() -> None:
    fixtures = ensure_compaction_fixtures()
    for name, fixture in fixtures.items():
        snapshot = fixture.initial_snapshot
        print(f"{name}.table_ref={fixture.table_ref}")
        print(f"{name}.location={fixture.location}")
        print(f"{name}.row_count={snapshot.row_count}")  # type: ignore[attr-defined]
        print(f"{name}.data_file_count={snapshot.data_file_count}")  # type: ignore[attr-defined]

    _seed_mor_fixtures()


if __name__ == "__main__":
    main()
