#!/usr/bin/env python3
"""Pin a new image digest into deploy/helm/cryo/values-prod.yaml, everywhere it needs to go.

Built after a real incident, 2026-08-10: a manual edit updated `migration.image.digest` and
`native.image.digest` to a new build but missed the top-level `image.digest` -- three separate
find-and-replace edits, one silently skipped. Result: api/worker/governor/scheduler would have
run one commit behind the native engine binary shipped in the exact same image. Caught before
deploy, not after, but the structural fix is to stop hand-editing three YAML paths that must
always move together.

This script updates all three digest fields that must always match the same image build:
  - image.digest
  - migration.image.digest
  - native.image.digest

It does NOT touch web.image.digest, spark.image.digest, or redis.image.digest -- those are
separate images with their own build/release cadence, deliberately never pinned by this script.

Text-based (not a full YAML parse-and-redump) on purpose: values-prod.yaml carries extensive
prose comments documenting *why* each setting is what it is -- a YAML round-trip would silently
destroy every one of them. Tracks which top-level key it's under by indentation, so it can find
the right `digest:` line inside each of the three blocks without needing the previous digest
value as a search anchor (unlike a plain find-and-replace, this still works correctly even if
the three digests have already drifted apart, which is exactly the state that caused the
original bug).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_TARGET_BLOCKS = ("image", "migration", "native")
_DIGEST_RE = re.compile(r'^(\s*)digest:\s*"sha256:[0-9a-f]{64}"\s*$')
_DIGEST_SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def pin_digest(text: str, new_digest: str) -> tuple[str, list[str]]:
    """Returns (updated_text, list of YAML paths that were updated)."""
    lines = text.split("\n")
    updated_paths: list[str] = []

    # Track the current top-level key (col-0 `key:` lines) and whether we're inside its
    # `image:` sub-block (for migration/native, which nest image: one level down).
    top_key: str | None = None
    in_image_block = False
    image_block_indent: int | None = None

    for i, line in enumerate(lines):
        stripped = line.rstrip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip(" "))

        if indent == 0 and stripped.endswith(":"):
            top_key = stripped[:-1]
            in_image_block = top_key == "image"  # top-level `image:` block starts immediately
            image_block_indent = 2 if in_image_block else None
            continue

        if top_key in _TARGET_BLOCKS and not in_image_block:
            # Look for a nested `image:` key (migration.image / native.image).
            if stripped.strip() == "image:":
                in_image_block = True
                image_block_indent = indent + 2
                continue

        if in_image_block and top_key in _TARGET_BLOCKS:
            match = _DIGEST_RE.match(line)
            if match and indent >= (image_block_indent or 0):
                lines[i] = f'{match.group(1)}digest: "{new_digest}"'
                path = "image.digest" if top_key == "image" else f"{top_key}.image.digest"
                updated_paths.append(path)
                in_image_block = False  # done with this block's digest
                continue
            # Left the image sub-block once indentation drops back to the parent key's level.
            if indent <= (2 if top_key == "image" else 2):
                if top_key != "image":
                    in_image_block = False

    return "\n".join(lines), updated_paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("digest", help="New digest, e.g. sha256:<64 hex chars>")
    parser.add_argument(
        "--file",
        default=str(Path(__file__).parent / "helm" / "cryo" / "values-prod.yaml"),
        help="Path to values-prod.yaml (default: deploy/helm/cryo/values-prod.yaml)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print what would change without writing"
    )
    args = parser.parse_args()

    digest = args.digest.strip()
    if not _DIGEST_SHA_RE.match(digest):
        print(f"error: {digest!r} doesn't look like a sha256:<64 hex chars> digest", file=sys.stderr)
        return 1

    path = Path(args.file)
    if not path.exists():
        print(f"error: {path} not found", file=sys.stderr)
        return 1

    original = path.read_text()
    updated, paths = pin_digest(original, digest)

    if len(paths) != 3:
        print(
            f"error: expected to update exactly 3 digest fields (image.digest, "
            f"migration.image.digest, native.image.digest), but updated {len(paths)}: {paths}. "
            "Refusing to write a partial update -- check values-prod.yaml's structure hasn't "
            "changed in a way this script doesn't understand.",
            file=sys.stderr,
        )
        return 1

    print(f"Would update: {', '.join(paths)}" if args.dry_run else f"Updated: {', '.join(paths)}")
    if args.dry_run:
        return 0

    path.write_text(updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
