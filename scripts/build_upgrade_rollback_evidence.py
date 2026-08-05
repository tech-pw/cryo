from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from cryo.evidence.upgrade import build_upgrade_rollback_evidence


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render and fingerprint the full Alembic upgrade/rollback graph."
    )
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    upgrade = _render("upgrade", "head")
    rollback = _render("downgrade", "head:base")
    evidence = build_upgrade_rollback_evidence(
        revision=args.revision,
        upgrade_sql=upgrade,
        rollback_sql=rollback,
    )
    if evidence.status != "repository_render_verified":
        raise SystemExit("migration SQL did not render as transaction-wrapped PostgreSQL")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence.to_dict(), indent=2, sort_keys=True) + "\n")


def _render(command: str, revision: str) -> bytes:
    result = subprocess.run(
        ("alembic", command, revision, "--sql"),
        check=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout


if __name__ == "__main__":
    main()
