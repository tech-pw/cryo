from __future__ import annotations

import argparse
import ast
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from cryo.capabilities import CAPABILITY_MANIFEST

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_FUNCTION_RE = re.compile(r"^\s*def test_", re.MULTILINE)
ANTD_IMPORT_RE = re.compile(r"""(?:from\s+|require\()\s*["']antd(?:/[^"']*)?["']""")


def _source_files(root: Path, relative: str, suffixes: set[str]) -> tuple[Path, ...]:
    base = root / relative
    return tuple(
        sorted(
            path
            for path in base.rglob("*")
            if path.is_file()
            and path.suffix in suffixes
            and "__pycache__" not in path.parts
            and path.name != "openapi.d.ts"
        )
    )


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _package_sizes(root: Path, files: tuple[Path, ...]) -> list[dict[str, object]]:
    totals: defaultdict[str, list[int]] = defaultdict(lambda: [0, 0])
    for path in files:
        relative = path.relative_to(root)
        package_depth = 3 if relative.parts[:2] == ("web", "src") else 2
        package = (
            "/".join(relative.parts[:package_depth])
            if len(relative.parts) > 1
            else relative.parts[0]
        )
        totals[package][0] += 1
        totals[package][1] += _line_count(path)
    return [
        {"package": package, "files": counts[0], "lines": counts[1]}
        for package, counts in sorted(
            totals.items(),
            key=lambda item: (-item[1][1], item[0]),
        )
    ]


def _top_files(root: Path, files: tuple[Path, ...], limit: int = 20) -> list[dict[str, object]]:
    ranked = sorted(
        ((path.relative_to(root).as_posix(), _line_count(path)) for path in files),
        key=lambda item: (-item[1], item[0]),
    )
    return [{"path": path, "lines": lines} for path, lines in ranked[:limit]]


def _assignment_value(module: ast.Module, name: str) -> object | None:
    for node in module.body:
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                value = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                value = node.value
        if value is not None:
            evaluated: object = ast.literal_eval(value)
            return evaluated
    return None


def _migration_snapshot(root: Path) -> dict[str, object]:
    migration_files = tuple(sorted((root / "migrations" / "versions").glob("*.py")))
    revisions: set[str] = set()
    parents: set[str] = set()
    for path in migration_files:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        revision = _assignment_value(module, "revision")
        if isinstance(revision, str):
            revisions.add(revision)
        down_revision = _assignment_value(module, "down_revision")
        if isinstance(down_revision, str):
            parents.add(down_revision)
        elif isinstance(down_revision, tuple):
            parents.update(item for item in down_revision if isinstance(item, str))
    return {
        "files": len(migration_files),
        "revisions": len(revisions),
        "heads": sorted(revisions - parents),
    }


def _frontend_stack_snapshot(root: Path, files: tuple[Path, ...]) -> dict[str, object]:
    antd_imports = [
        path.relative_to(root).as_posix()
        for path in files
        if ANTD_IMPORT_RE.search(path.read_text(encoding="utf-8"))
    ]
    package = json.loads((root / "web" / "package.json").read_text(encoding="utf-8"))
    dependencies = package.get("dependencies", {})
    if not isinstance(dependencies, dict):
        raise TypeError("web/package.json dependencies must be an object")
    return {
        "antd_import_files": antd_imports,
        "antd_dependency_present": "antd" in dependencies,
        "radix_packages": len(
            [
                name
                for name in dependencies
                if isinstance(name, str) and name.startswith("@radix-ui/")
            ]
        ),
    }


def build_snapshot(root: Path = REPO_ROOT) -> dict[str, object]:
    python_files = _source_files(root, "cryo", {".py"})
    backend_test_files = _source_files(root, "tests", {".py"})
    frontend_files = _source_files(root, "web/src", {".ts", ".tsx"})
    frontend_test_files = tuple(
        path for path in frontend_files if ".test." in path.name or ".spec." in path.name
    )
    handwritten_files = python_files + frontend_files

    support_counts = Counter(cell.support.value for cell in CAPABILITY_MANIFEST.matrix)
    maturity_counts = Counter(cell.maturity.value for cell in CAPABILITY_MANIFEST.matrix)

    return {
        "schema_version": 1,
        "scope": "repository-owned reproducible metrics; production facts live in the baseline doc",
        "capabilities": {
            "manifest_schema": CAPABILITY_MANIFEST.schema_version,
            "engines": len(CAPABILITY_MANIFEST.engines),
            "operations": len(CAPABILITY_MANIFEST.operations),
            "catalogs": len(CAPABILITY_MANIFEST.catalogs),
            "matrix_cells": len(CAPABILITY_MANIFEST.matrix),
            "expected_matrix_cells": (
                len(CAPABILITY_MANIFEST.engines) * len(CAPABILITY_MANIFEST.operations)
            ),
            "support_counts": dict(sorted(support_counts.items())),
            "maturity_counts": dict(sorted(maturity_counts.items())),
        },
        "source": {
            "backend_files": len(python_files),
            "backend_lines": sum(_line_count(path) for path in python_files),
            "frontend_files": len(frontend_files),
            "frontend_lines": sum(_line_count(path) for path in frontend_files),
            "backend_test_files": len(backend_test_files),
            "backend_test_functions": sum(
                len(TEST_FUNCTION_RE.findall(path.read_text(encoding="utf-8")))
                for path in backend_test_files
            ),
            "frontend_test_files": len(frontend_test_files),
        },
        "packages": _package_sizes(root, handwritten_files),
        "hotspots": _top_files(root, handwritten_files),
        "migrations": _migration_snapshot(root),
        "frontend_stack": _frontend_stack_snapshot(root, frontend_files),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture Cryo's reproducible repository baseline")
    parser.add_argument("--output", type=Path, help="Write JSON to this path instead of stdout")
    args = parser.parse_args()
    rendered = json.dumps(build_snapshot(), indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
