"""Native engine plan rendering (preview / worker parity with Trino/Spark renderers)."""

from __future__ import annotations

from cryo.domain.models import MaintenancePlan
from cryo.engines.base import RenderedStatement
from cryo.engines.native.engine import NativeEngine


def render(plan: MaintenancePlan) -> list[RenderedStatement]:
    return NativeEngine(config={}).render(plan)
