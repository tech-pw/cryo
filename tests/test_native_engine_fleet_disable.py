"""Tests for the fleet-wide native-engine kill switch.

Real incident, 2026-09-16: the native (iceberg-go) engine has no independent verification that
its self-reported success actually mutated the Iceberg table (gold_dbt_poll_dashboard_view ran
25 minutes, reported success on every operation, committed zero snapshots -- see
test_native_watcher_zero_work_noop.py for the narrower per-operation guard added the same day).
Until that trust boundary is fixed, the decision was made to disable the native engine
fleet-wide and run everything through the Spark fallback instead.

`native_engine_enabled=False` (cryo/config.py) already blocked cryo.engines.factory.create_engine
from building a NativeEngine, but that alone was incomplete in two ways this fix closes:

1. cryo/governor/governor.py's `use_native_k8s` gate didn't check the flag, so the governor kept
   spinning up native K8s pods that would then discover mid-flight (via resolve_native_routing)
   that every operation needs to reroute -- wasting a pod launch to learn that.
2. cryo/engines/native/routing.py's resolve_native_routing had no way to reroute *every*
   operation to fallback in one shot -- it only ever knew how to fallback individual
   operation types (optimize_manifests, compute_statistics, etc).
3. cryo/worker/worker.py's process_next constructed the engine via self._engine_factory(
   destination) before resolve_native_routing ever got a chance to run, so create_engine's
   existing ValueError fired first and every job against a native destination hard-failed
   instead of rerouting to Spark.
"""

from __future__ import annotations

import pytest

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.native.routing import (
    NATIVE_ENGINE_DISABLED_DETAIL,
    resolve_native_routing,
)


def _deep_plan() -> MaintenancePlan:
    return MaintenancePlan(
        table_ref="rest.demo.orders",
        mode=Mode.DEEP,
        operations=(
            Operation(OperationType.EXPIRE_SNAPSHOTS, {"retention_days": 7}),
            Operation(OperationType.REMOVE_ORPHAN_FILES, {"orphan_min_age_hours": 72}),
            Operation(OperationType.OPTIMIZE, {"file_size_mb": 256}),
        ),
    )


class TestResolveNativeRoutingGloballyDisabled:
    def test_all_operations_route_to_fallback_when_disabled_and_fallback_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "cryo.engines.native.routing._native_engine_globally_enabled", lambda: False
        )
        plan = _deep_plan()
        cfg = {"sql_fallback_destination_id": "spark-fallback-1"}
        result = resolve_native_routing(plan, {"total_delete_files": 0}, cfg)

        assert result.native_operations == ()
        assert len(result.fallback_operations) == len(plan.operations)
        assert result.skipped_operations == ()
        for op, detail in result.fallback_operations:
            assert detail == NATIVE_ENGINE_DISABLED_DETAIL
            assert op in plan.operations

    def test_all_operations_are_skipped_not_fallback_when_disabled_with_no_fallback_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No sql_fallback_destination_id configured -- this must not silently drop the work
        as a native fallback op with no detail; it must show up as skipped so it's visible."""
        monkeypatch.setattr(
            "cryo.engines.native.routing._native_engine_globally_enabled", lambda: False
        )
        plan = _deep_plan()
        result = resolve_native_routing(plan, {"total_delete_files": 0}, {})

        assert result.native_operations == ()
        assert result.fallback_operations == ()
        assert len(result.skipped_operations) == len(plan.operations)
        for op, detail in result.skipped_operations:
            assert detail == NATIVE_ENGINE_DISABLED_DETAIL

    def test_enabled_flag_preserves_normal_routing_behavior(self) -> None:
        """Baseline: this fix must not change routing at all when the flag is on (the default) --
        a table with delete files still gets OPTIMIZE routed to fallback for the pre-existing
        reason (has_nonzero_delete_files), not the new blanket-disabled reason."""
        plan = _deep_plan()
        cfg = {"sql_fallback_destination_id": "spark-fallback-1"}
        result = resolve_native_routing(plan, {"total_delete_files": 5}, cfg)

        fallback_details = {detail for _, detail in result.fallback_operations}
        assert NATIVE_ENGINE_DISABLED_DETAIL not in fallback_details
        # expire_snapshots and remove_orphan_files are native-supported regardless of delete files.
        native_types = {op.type for op in result.native_operations}
        assert OperationType.EXPIRE_SNAPSHOTS in native_types
        assert OperationType.REMOVE_ORPHAN_FILES in native_types


class TestUseNativeK8sRespectsGlobalDisable:
    def test_governor_use_native_k8s_is_false_when_engine_globally_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Direct check of the governor's gating expression -- destination_uses_k8s_dispatch
        returning True must not be enough on its own once native_engine_enabled is False."""
        from types import SimpleNamespace

        from cryo.config import Settings
        from cryo.governor.native_admit import destination_uses_k8s_dispatch

        destination = SimpleNamespace(
            engine="native", config={"k8s_dispatch": True}
        )
        assert destination_uses_k8s_dispatch(destination) is True

        disabled_settings = Settings(native_engine_enabled=False)
        assert disabled_settings.native_engine_enabled is False

        enabled_settings = Settings(native_engine_enabled=True)
        assert enabled_settings.native_engine_enabled is True


class TestWorkerRedirectsToSqlFallbackWhenDisabled:
    def test_default_engine_factory_still_raises_when_disabled_and_no_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """create_engine's existing guard (unchanged) still refuses to build a bare NativeEngine
        when the flag is off -- this test pins that baseline so the worker-level redirect (which
        needs a real store/session to resolve the fallback destination, exercised via the
        routing-level tests above) is understood as strictly additive, not a replacement."""
        from cryo.domain.enums import EngineType
        from cryo.engines.factory import create_engine

        # get_settings() is @lru_cache'd (cryo/config.py) -- a real, intentional production
        # behavior (settings are read once per process; changing CRYO_NATIVE_ENGINE_ENABLED
        # requires a pod restart/redeploy, same as every other env-driven setting here). A
        # real incident this exact test caused, 2026-09-16: an earlier version called
        # get_settings.cache_clear() directly, which doesn't just clear this one flag -- it
        # evicts the *entire* cached Settings singleton, including jwt_secret/secret_key that
        # tests/auth_helpers.py's module-level VIEWER_HEADERS/OPERATOR_HEADERS/ADMIN_HEADERS
        # (issued once at import time) implicitly depend on staying resolved -- 46 unrelated
        # API tests started failing with 401s the instant this test ran anywhere earlier in
        # the same pytest session, since order-dependent global cache eviction is exactly the
        # kind of blast-radius mistake the rest of this session has been fixing all day.
        # create_engine does `from cryo.config import get_settings` as a *local* import inside
        # the function body, so there's no cryo.engines.factory.get_settings module attribute
        # to patch -- the name it binds resolves fresh from cryo.config at call time. monkeypatch
        # replacing cryo.config.get_settings itself (auto-reverted at test teardown) intercepts
        # exactly that resolution without ever touching the real @lru_cache'd singleton other
        # tests' auth headers depend on staying resolved.
        import cryo.config
        from cryo.config import Settings

        monkeypatch.setattr(
            cryo.config, "get_settings", lambda: Settings(native_engine_enabled=False)
        )
        with pytest.raises(ValueError, match="Native engine is disabled"):
            create_engine(EngineType.NATIVE, {})
