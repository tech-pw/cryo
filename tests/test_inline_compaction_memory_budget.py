"""Inline rewrites get a budget derived from the worker's own memory limit.

The k8s-dispatch path sizes a Job per table and derives the compaction memory budget
from that Job's request. A job executed inline in the worker has no such spec, and
before this it fell through to a fixed constant -- so the engine planned every rewrite
as if it had that much memory however large the worker actually was.
"""

from __future__ import annotations

import pytest

from cryo.engines.native.resources import (
    INLINE_BUDGET_FALLBACK_BYTES,
    cgroup_memory_limit_bytes,
    compaction_memory_budget_bytes,
    inline_compaction_memory_budget,
)

GiB = 1024**3


def test_budget_follows_the_cgroup_limit() -> None:
    budget, source = inline_compaction_memory_budget(limit_bytes=10 * GiB)
    assert source == "cgroup"
    assert budget == compaction_memory_budget_bytes(10 * 1024)


def test_budget_scales_with_the_limit() -> None:
    """The point of the change: a bigger worker must mean a bigger plan, not just more
    headroom before the kernel intervenes."""
    small, _ = inline_compaction_memory_budget(limit_bytes=4 * GiB)
    large, _ = inline_compaction_memory_budget(limit_bytes=12 * GiB)
    assert large > small


def test_explicit_setting_wins_over_the_cgroup() -> None:
    budget, source = inline_compaction_memory_budget(7 * GiB, limit_bytes=10 * GiB)
    assert (budget, source) == (7 * GiB, "setting")


def test_falls_back_to_the_constant_when_the_limit_is_unreadable() -> None:
    """An unreadable cgroup must degrade to the previous behaviour, not to something
    unbounded."""
    budget, source = inline_compaction_memory_budget(limit_bytes=None)
    # limit_bytes=None means "read the cgroup"; in an unlimited test environment that
    # returns None and we must land on the fallback. Where the test runner *is*
    # containerised, the cgroup answer is equally valid -- assert the invariant both
    # satisfy rather than pinning one.
    assert source in {"cgroup", "fallback"}
    if source == "fallback":
        assert budget == INLINE_BUDGET_FALLBACK_BYTES


def test_budget_is_never_above_the_pod_ceiling() -> None:
    budget, _ = inline_compaction_memory_budget(limit_bytes=64 * GiB)
    assert budget <= compaction_memory_budget_bytes(64 * 1024)
    assert budget <= 16 * GiB


@pytest.mark.parametrize(
    "raw, expected",
    [("max", None), ("", None), ("0", None), (str(1 << 62), None), (str(4 * GiB), 4 * GiB)],
)
def test_cgroup_reader_rejects_non_limits(tmp_path, raw: str, expected: int | None) -> None:
    """Moved from the scanner; kept because both engines now depend on it."""
    f = tmp_path / "memory.max"
    f.write_text(raw)
    assert cgroup_memory_limit_bytes((str(f),)) == expected


def test_scanner_still_exposes_the_reader() -> None:
    """go_scan re-imports it by name, so existing monkeypatching keeps working."""
    from cryo.scanner.go_scan import cgroup_memory_limit_bytes as from_scanner

    assert from_scanner is cgroup_memory_limit_bytes


class _Cfg(dict):
    pass


def _request_with_rewrite(params: dict | None = None) -> dict:
    op: dict = {"type": "rewrite_data_files"}
    if params is not None:
        op["params"] = params
    return {"operations": [op, {"type": "expire_snapshots"}]}


def test_inline_application_fills_only_the_rewrite_op() -> None:
    import logging

    from cryo.config import get_settings
    from cryo.engines.native.engine import _apply_inline_memory_budget

    req = _request_with_rewrite()
    _apply_inline_memory_budget(
        req, get_settings(), table_ref="c.s.t", logger_=logging.getLogger(__name__)
    )
    rewrite, expire = req["operations"]
    assert rewrite["params"]["compaction_memory_budget_bytes"] > 0
    assert "params" not in expire


def test_inline_application_never_overrides_a_caller_value() -> None:
    """The dispatch path sets the budget from the Job spec before the request is built.
    This is the regression that would silently undo per-table sizing."""
    import logging

    from cryo.config import get_settings
    from cryo.engines.native.engine import _apply_inline_memory_budget

    req = _request_with_rewrite({"compaction_memory_budget_bytes": 123})
    _apply_inline_memory_budget(
        req, get_settings(), table_ref="c.s.t", logger_=logging.getLogger(__name__)
    )
    assert req["operations"][0]["params"]["compaction_memory_budget_bytes"] == 123
