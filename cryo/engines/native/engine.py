from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryo.config import Settings, get_settings
from cryo.domain.enums import EngineType
from cryo.domain.models import CapacitySnapshot, MaintenancePlan
from cryo.engines.base import ExecCtx, ExecResult, RenderedStatement
from cryo.engines.native.payload import plan_to_payload


@dataclass
class NativeEngine:
    """Subprocess adapter for the cryo-engine Rust binary."""

    config: dict[str, Any]
    settings: Settings | None = None

    type: EngineType = EngineType.NATIVE

    def __post_init__(self) -> None:
        self.settings = self.settings or get_settings()
        self._binary = str(
            self.config.get("binary") or self.config.get("engine_binary") or "cryo-engine"
        )
        self._slot_cap = int(self.config.get("slot_cap") or self.settings.default_native_slot_cap)

    def probe_capacity(self) -> CapacitySnapshot:
        return CapacitySnapshot(capacity=self._slot_cap, used=0)

    def render(self, plan: MaintenancePlan) -> list[RenderedStatement]:
        payload = plan_to_payload(plan)
        return [
            RenderedStatement(
                action=operation.type,
                sql="",
                engine=EngineType.NATIVE,
                payload={"operation": operation.type.value, **payload},
            )
            for operation in plan.operations
        ]

    def session_setup(self) -> list[str]:
        return []

    def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> ExecResult:
        start = time.monotonic()
        request = dict(stmt.payload)
        if ctx and ctx.job_id:
            request["job_id"] = str(ctx.job_id)
        try:
            completed = subprocess.run(
                [self._binary, "execute"],
                input=json.dumps(request),
                capture_output=True,
                text=True,
                check=False,
                timeout=int(self.config.get("timeout_seconds") or 3600),
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            if completed.returncode == 0:
                detail = completed.stdout.strip() or "native engine completed"
                return ExecResult(ok=True, detail=detail, duration_ms=duration_ms)
            error = completed.stderr.strip() or completed.stdout.strip() or "native engine failed"
            return ExecResult(ok=False, error=error, duration_ms=duration_ms)
        except FileNotFoundError:
            return ExecResult(
                ok=False,
                error=f"Native engine binary not found: {self._binary}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except subprocess.TimeoutExpired:
            return ExecResult(
                ok=False,
                error="Native engine timed out",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as exc:
            return ExecResult(
                ok=False,
                error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )


def default_binary_path() -> Path:
    return (
        Path(__file__).resolve().parents[2] / "cryo-engine" / "target" / "release" / "cryo-engine"
    )
