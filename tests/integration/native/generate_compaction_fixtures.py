#!/usr/bin/env python3
"""Seed COW compaction fixtures on the local REST catalog + MinIO."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.integration.native.compaction_fixtures import ensure_compaction_fixtures


def main() -> None:
    fixtures = ensure_compaction_fixtures()
    for name, fixture in fixtures.items():
        snapshot = fixture.initial_snapshot
        print(f"{name}.table_ref={fixture.table_ref}")
        print(f"{name}.location={fixture.location}")
        print(f"{name}.row_count={snapshot.row_count}")  # type: ignore[attr-defined]
        print(f"{name}.data_file_count={snapshot.data_file_count}")  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()
