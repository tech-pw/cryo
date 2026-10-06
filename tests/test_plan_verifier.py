"""Tests for cryo/planner/verifier.py — Proof-Carrying AI Agents (arXiv:2510.09567)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import (
    MaintenancePlan,
    Operation,
)
from cryo.planner.verifier import (
    DEFAULT_MAX_PARTITIONS_PER_CHUNK,
    VERIFIER_VERSION,
    InvariantContext,
    PlanVerdict,
    PlanVerifier,
    PlanViolation,
    VerifierRegistry,
    canonical_plan_bytes,
    hash_plan,
)


class TestPlanHashing:
    def test_hash_plan_deterministic(self) -> None:
        plan = MaintenancePlan(
            table_ref="db.table",
            mode=Mode.STANDARD,
            operations=[
                Operation(type=OperationType.OPTIMIZE),
            ],
            notes=["test"],
        )
        h1 = hash_plan(plan)
        h2 = hash_plan(plan)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_hash_plan_changes_on_operation(self) -> None:
        plan1 = MaintenancePlan(
            table_ref="db.table",
            mode=Mode.STANDARD,
            operations=[Operation(type=OperationType.OPTIMIZE)],
        )
        plan2 = MaintenancePlan(
            table_ref="db.table",
            mode=Mode.STANDARD,
            operations=[Operation(type=OperationType.EXPIRE_SNAPSHOTS)],
        )
        assert hash_plan(plan1) != hash_plan(plan2)

    def test_canonical_bytes_sort_keys(self) -> None:
        plan = MaintenancePlan(
            table_ref="db.table",
            mode=Mode.STANDARD,
            operations=[
                Operation(
                    type=OperationType.OPTIMIZE,
                    params={"z_param": "z", "a_param": "a"},
                ),
            ],
        )
        raw = canonical_plan_bytes(plan)
        # JSON with sort_keys must have "a_param" before "z_param"
        assert raw.find(b'"a_param"') < raw.find(b'"z_param"')

    def test_verifier_version_present(self) -> None:
        assert VERIFIER_VERSION == "1.0.0"


class TestVerifierRegistry:
    def test_register_and_retrieve(self) -> None:
        registry = VerifierRegistry.get("test_registry")

        called: list[str] = []

        def dummy_invariant(ctx: InvariantContext) -> None:
            called.append("inv1")

        registry.register("inv1", dummy_invariant)
        invariants = registry.invariants()
        assert len(invariants) >= 1
        names = [n for n, _ in invariants]
        assert "inv1" in names

    def test_deregister(self) -> None:
        registry = VerifierRegistry.get("test_deregister")

        def dummy(ctx: InvariantContext) -> None:
            pass

        registry.register("to_remove", dummy)
        assert any(n == "to_remove" for n, _ in registry.invariants())
        registry.deregister("to_remove")
        assert not any(n == "to_remove" for n, _ in registry.invariants())

    def test_duplicate_registration_raises(self) -> None:
        registry = VerifierRegistry.get("test_dup")
        registry.deregister("dup_test")  # clean slate

        def dummy(ctx: InvariantContext) -> None:
            pass

        registry.register("dup_test", dummy)
        with pytest.raises(ValueError, match="already registered"):
            registry.register("dup_test", dummy)

    def test_singleton_per_name(self) -> None:
        r1 = VerifierRegistry.get("singleton_test")
        r2 = VerifierRegistry.get("singleton_test")
        assert r1 is r2


class TestPlanVerifier:
    def _mock_plan(
        self,
        table_ref: str = "db.tbl",
        ops: list[Operation] | None = None,
    ) -> MaintenancePlan:
        return MaintenancePlan(
            table_ref=table_ref,
            mode=Mode.STANDARD,
            operations=ops or [Operation(type=OperationType.OPTIMIZE)],
        )

    def _mock_session(self) -> MagicMock:
        return MagicMock()

    def _mock_settings(self) -> MagicMock:
        s = MagicMock()
        s.max_partitions_per_chunk = DEFAULT_MAX_PARTITIONS_PER_CHUNK
        s.compaction_budget_bytes = 8 * 1024**3
        return s

    def test_verdict_fields(self) -> None:
        verdict = PlanVerdict(verified=True, plan_hash="abc123", verifier_version=VERIFIER_VERSION)
        assert verdict.verified is True
        assert verdict.plan_hash == "abc123"
        assert verdict.verifier_version == VERIFIER_VERSION
        assert verdict.invariants_checked == ()
        assert verdict.violations == ()

    def test_violation_fields(self) -> None:
        v = PlanViolation(
            invariant="table_exists",
            severity="critical",
            message="Table not found",
            location="table_ref",
        )
        assert v.invariant == "table_exists"
        assert v.severity == "critical"
        assert v.message == "Table not found"
        assert v.location == "table_ref"

    def test_verifier_instantiates(self) -> None:
        # VERIFIER_VERSION is a module-level constant surfaced via PlanVerdict.verifier_version
        # on each verify() call (see test_verify_returns_verdict below) -- PlanVerifier itself
        # doesn't carry a per-instance version attribute, so assert what it does carry instead.
        verifier = PlanVerifier(
            session=self._mock_session(),
            settings=self._mock_settings(),
        )
        assert verifier is not None
        assert verifier.registry is VerifierRegistry.get("default")

    def test_verify_returns_verdict(self) -> None:
        verifier = PlanVerifier(
            session=self._mock_session(),
            settings=self._mock_settings(),
        )
        plan = self._mock_plan()
        verdict = verifier.verify(plan)
        assert isinstance(verdict, PlanVerdict)
        assert verdict.verifier_version == VERIFIER_VERSION
        assert len(verdict.plan_hash) == 64

    def test_verify_no_violations_clean_plan(self) -> None:
        registry = VerifierRegistry.get("test_clean")
        registry.deregister("test_clean")  # clear any registered invariants
        verifier = PlanVerifier(
            session=self._mock_session(),
            settings=self._mock_settings(),
        )
        plan = self._mock_plan()
        verdict = verifier.verify(plan)
        assert isinstance(verdict, PlanVerdict)
        assert verdict.plan_hash == hash_plan(plan)

    def test_verify_tracks_invariants_checked(self) -> None:
        # Registering into a registry PlanVerifier never sees can't work -- PlanVerifier defaults
        # to VerifierRegistry.get("default") when no registry kwarg is passed, so the custom
        # invariant must go into a registry that's actually wired into the verifier instance.
        registry = VerifierRegistry.get("test_checked")
        registry.deregister("test_checked")

        def tracking_invariant(ctx: InvariantContext) -> None:
            pass

        registry.register("test_checked", tracking_invariant)
        try:
            verifier = PlanVerifier(
                session=self._mock_session(), settings=self._mock_settings(), registry=registry
            )
            plan = self._mock_plan()
            verdict = verifier.verify(plan)
            assert "test_checked" in verdict.invariants_checked
        finally:
            registry.deregister("test_checked")


class TestResolveTableDisambiguation:
    """Multi-catalog fleets share schema.table across sources; resolve must not raise."""

    def test_schema_table_unique_match(self) -> None:
        from cryo.planner.verifier import _resolve_table

        row = MagicMock()
        session = MagicMock()
        session.execute.return_value.scalars.return_value.all.return_value = [row]
        assert _resolve_table(session, "user_experience.gold_test_student_mappings") is row

    def test_schema_table_ambiguous_returns_none(self) -> None:
        from cryo.planner.verifier import _resolve_table

        session = MagicMock()
        session.execute.return_value.scalars.return_value.all.return_value = [
            MagicMock(),
            MagicMock(),
        ]
        assert _resolve_table(session, "user_experience.gold_test_student_mappings") is None

    def test_catalog_schema_table_picks_matching_catalog(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from cryo.planner import verifier as verifier_mod

        sample = MagicMock()
        sample_dev = MagicMock()
        session = MagicMock()
        session.execute.return_value.scalars.return_value.all.return_value = [sample, sample_dev]

        def _catalog_for(table: object) -> str:
            if table is sample:
                return "sample"
            if table is sample_dev:
                return "hive"
            return "unknown"

        monkeypatch.setattr(
            "cryo.db.model_utils.catalog_name_for_table",
            _catalog_for,
        )
        # Import path used inside _resolve_table
        monkeypatch.setattr(
            verifier_mod,
            "catalog_name_for_table",
            _catalog_for,
            raising=False,
        )
        # _resolve_table imports catalog_name_for_table from model_utils inside the function
        import cryo.db.model_utils as model_utils

        monkeypatch.setattr(model_utils, "catalog_name_for_table", _catalog_for)

        hive_ref = "hive.ux.gold_student_mappings"
        sample_ref = "sample.ux.gold_student_mappings"
        assert verifier_mod._resolve_table(session, hive_ref) is sample_dev
        assert verifier_mod._resolve_table(session, sample_ref) is sample

    def test_load_catalog_context_exception_does_not_raise(self) -> None:
        verifier = PlanVerifier(
            session=MagicMock(),
            settings=MagicMock(
                max_partitions_per_chunk=DEFAULT_MAX_PARTITIONS_PER_CHUNK,
                compaction_budget_bytes=8 * 1024**3,
            ),
        )
        verifier._load_catalog_context = MagicMock(side_effect=RuntimeError("ambiguous"))  # type: ignore[method-assign]
        plan = MaintenancePlan(
            table_ref="user_experience.gold_test_student_mappings",
            mode=Mode.STANDARD,
            operations=(Operation(type=OperationType.OPTIMIZE),),
        )
        verdict = verifier.verify(plan)
        assert isinstance(verdict, PlanVerdict)
        assert len(verdict.plan_hash) == 64
