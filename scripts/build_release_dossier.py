from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from cryo.evidence.release import write_release_dossier


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a deterministic, fail-closed Cryo release evidence dossier."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    revision = args.revision or _git_revision(args.root)
    dossier = write_release_dossier(
        args.root,
        args.output,
        version=args.version,
        revision=revision,
    )
    return 0 if dossier["graduation"]["repository_complete"] else 1


def _git_revision(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
