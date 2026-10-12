#!/usr/bin/env python3
"""Seed the GB-scale dense compaction perf fixture on local REST + MinIO."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.integration.native.compaction_fixtures import (  # noqa: E402
    ensure_perf_compaction_fixture,
)


def main() -> None:
    fixture = ensure_perf_compaction_fixture()
    snap = fixture.initial_snapshot
    print(
        f"seeded {fixture.table_ref} location={fixture.location} "
        f"files={snap.data_file_count} rows={snap.row_count}"
    )


if __name__ == "__main__":
    main()
