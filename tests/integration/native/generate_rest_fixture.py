#!/usr/bin/env python3
"""Seed the REST catalog native e2e fixture (multi-snapshot table + orphan object)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.integration.native.rest_env import ensure_rest_fixture


def main() -> None:
    fixture = ensure_rest_fixture()
    print(f"table_ref={fixture.table_ref}")
    print(f"location={fixture.location}")
    print(f"orphan_uri={fixture.orphan_uri}")
    print(f"orphan_bytes={fixture.orphan_bytes}")
    print(f"snapshot_count={fixture.snapshot_count}")


if __name__ == "__main__":
    main()
