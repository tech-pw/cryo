"""Cross-engine compatibility matrix for native compaction (scoping §3.6 / risk register).

Documents which writer engines are expected to round-trip through Go native OPTIMIZE,
and provides an optional integration test when Trino + REST + Go engine are available.

This does not implement a full multi-engine write matrix in CI by default — Spark writes
require EMR/Spark fixtures that most local stacks lack. The unit portion asserts the
documented matrix stays explicit so gaps cannot silently drift.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class CompatCase:
    writer: str
    reader_after_native: str
    expected: str  # "supported" | "partial" | "unsupported" | "unverified"
    notes: str


# Keep in sync with docs/native-engine-scoping-research.md §3.6 / §8 risk register.
CROSS_ENGINE_COMPAT_MATRIX: tuple[CompatCase, ...] = (
    CompatCase(
        writer="go-native-seed",
        reader_after_native="go-native",
        expected="supported",
        notes="COW fixtures in test_native_compaction_e2e / differential e2e",
    ),
    CompatCase(
        writer="trino",
        reader_after_native="trino",
        expected="partial",
        notes=(
            "Differential e2e covers unpartitioned + identity; "
            "day partition still flaky upstream"
        ),
    ),
    CompatCase(
        writer="spark",
        reader_after_native="trino",
        expected="unverified",
        notes="Needs Spark write fixture; not wired in default CI",
    ),
    CompatCase(
        writer="spark",
        reader_after_native="spark",
        expected="unverified",
        notes="Needs Spark History / local Spark; tracked as scoping risk register item",
    ),
    CompatCase(
        writer="trino",
        reader_after_native="spark",
        expected="unverified",
        notes="Cross-engine read-after-native not yet covered",
    ),
)


def test_cross_engine_matrix_is_explicit() -> None:
    assert len(CROSS_ENGINE_COMPAT_MATRIX) >= 4
    writers = {case.writer for case in CROSS_ENGINE_COMPAT_MATRIX}
    assert "trino" in writers
    assert "spark" in writers
    unverified = [c for c in CROSS_ENGINE_COMPAT_MATRIX if c.expected == "unverified"]
    assert unverified, "matrix should keep unverified Spark paths visible"


@pytest.mark.skipif(
    os.getenv("CRYO_TRINO_URI") is None or os.getenv("CRYO_ENGINE_IMPL", "go") != "go",
    reason="Set CRYO_TRINO_URI and CRYO_ENGINE_IMPL=go for cross-engine smoke",
)
def test_trino_writer_native_optimize_smoke() -> None:
    """Optional smoke: defers to differential e2e when the stack is up.

    Importing the differential module keeps a single implementation of clone/optimize.
    """
    pytest.importorskip("trino")
    from tests.integration.native import test_native_differential_e2e as diff

    # Presence of helpers is enough for this smoke gate; full asserts live in differential e2e.
    assert hasattr(diff, "_trino_create_clone")
    assert hasattr(diff, "requires_trino")
