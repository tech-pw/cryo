"""Register Ask Cryo read tools. Idempotent. Tier 3 is never registered."""

from __future__ import annotations

from cryo.ai.tools.actions import SPECS as ACTION_SPECS
from cryo.ai.tools.read_capacity import SPECS as CAPACITY_SPECS
from cryo.ai.tools.read_cost import SPECS as COST_SPECS
from cryo.ai.tools.read_decisions import SPECS as DECISION_SPECS
from cryo.ai.tools.read_docs import SPECS as DOC_SPECS
from cryo.ai.tools.read_health import SPECS as HEALTH_SPECS
from cryo.ai.tools.read_identity import SPECS as IDENTITY_SPECS
from cryo.ai.tools.read_layout import SPECS as LAYOUT_SPECS
from cryo.ai.tools.read_metadata import SPECS as METADATA_SPECS
from cryo.ai.tools.read_quality import SPECS as QUALITY_SPECS
from cryo.ai.tools.read_runs import SPECS as RUN_SPECS
from cryo.ai.tools.read_snapshots import SPECS as SNAPSHOT_SPECS
from cryo.ai.tools.registry import ToolSpec, get_registry

_ALL: tuple[ToolSpec, ...] = (
    *IDENTITY_SPECS,
    *HEALTH_SPECS,
    *METADATA_SPECS,
    *LAYOUT_SPECS,
    *SNAPSHOT_SPECS,
    *DECISION_SPECS,
    *RUN_SPECS,
    *CAPACITY_SPECS,
    *COST_SPECS,
    *QUALITY_SPECS,
    *DOC_SPECS,
    *ACTION_SPECS,
)


def register_builtin_tools() -> None:
    """Idempotent per-spec, not per-call: the early-return this used to have (skip the whole
    loop once two sentinel tools were present) meant a test that registered a fake spec under
    a real tool's name -- overwriting it in the shared registry singleton -- and later popped
    it out on teardown left that tool permanently unregistered for the rest of the process,
    because this function saw the sentinels already present and never got to the per-spec
    check that would have restored it. Confirmed live 2026-09-29 on get_lake_health. Always
    walk the full list; the `is None` check below already makes each spec's registration
    itself cheap and idempotent, so there's no real cost to dropping the early exit."""
    registry = get_registry()
    for spec in _ALL:
        if spec.tier >= 3:
            continue
        if registry.get(spec.name) is None:
            registry.register(spec)
