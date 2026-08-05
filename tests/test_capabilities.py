from __future__ import annotations

from itertools import product
from pathlib import Path

from cryo.capabilities import (
    CAPABILITY_MANIFEST,
    EffectStrategy,
    ExecutionMode,
    MaturityTier,
    SupportStatus,
    capability_for,
)
from cryo.capabilities.documentation import render_capabilities_markdown
from cryo.domain.enums import EngineType, OperationType
from cryo.engines.native.routing import (
    NATIVE_ALWAYS_DISCONNECTED,
    NATIVE_BASE_OPERATIONS,
    native_fallback_supports,
)
from tests.auth_helpers import VIEWER_HEADERS

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_operation_matrix_is_exhaustive_and_unique() -> None:
    expected = set(product(EngineType, OperationType))
    actual = {(cell.engine, cell.operation) for cell in CAPABILITY_MANIFEST.matrix}

    assert actual == expected
    assert len(CAPABILITY_MANIFEST.matrix) == len(expected)
    assert all(cell.effect_strategy for cell in CAPABILITY_MANIFEST.matrix)
    assert {item.operation for item in CAPABILITY_MANIFEST.operations} == set(OperationType)
    assert {item.engine for item in CAPABILITY_MANIFEST.engines} == set(EngineType)


def test_manifest_records_known_engine_gaps_without_overclaiming() -> None:
    trino_minor = capability_for(EngineType.TRINO, OperationType.REWRITE_POSITION_DELETES)
    assert trino_minor.support is SupportStatus.UNSUPPORTED
    assert trino_minor.maturity is MaturityTier.BLOCKED
    assert trino_minor.execution_mode is ExecutionMode.NONE
    assert trino_minor.fallback_engines == (EngineType.SPARK_EMR,)

    native_optimize = capability_for(EngineType.NATIVE, OperationType.OPTIMIZE)
    assert native_optimize.support is SupportStatus.CONDITIONAL
    assert "engine_impl must be go." in native_optimize.runtime_conditions

    native_manifests = capability_for(
        EngineType.NATIVE,
        OperationType.OPTIMIZE_MANIFESTS,
    )
    assert native_manifests.support is SupportStatus.FALLBACK_REQUIRED
    assert native_manifests.effect_strategy is EffectStrategy.STABLE_REFERENCE_PROBE
    assert set(native_manifests.fallback_engines) == {
        EngineType.TRINO,
        EngineType.SPARK_EMR,
    }

    flink_optimize = capability_for(EngineType.FLINK, OperationType.OPTIMIZE)
    assert flink_optimize.support is SupportStatus.CONFIGURATION_ONLY
    assert flink_optimize.maturity is MaturityTier.EXPERIMENTAL
    assert flink_optimize.effect_strategy is EffectStrategy.NO_EXTERNAL_EFFECT

    native_orphans = capability_for(EngineType.NATIVE, OperationType.REMOVE_ORPHAN_FILES)
    assert native_orphans.effect_strategy is EffectStrategy.MANUAL_RECONCILIATION

    native_optimize = capability_for(EngineType.NATIVE, OperationType.OPTIMIZE)
    assert native_optimize.effect_strategy is EffectStrategy.CONDITIONAL_SNAPSHOT_COMMIT


def test_native_static_routing_is_derived_from_manifest() -> None:
    expected_direct = {
        cell.operation
        for cell in CAPABILITY_MANIFEST.matrix
        if cell.engine is EngineType.NATIVE and cell.support is SupportStatus.SUPPORTED
    }
    expected_fallback = {
        cell.operation
        for cell in CAPABILITY_MANIFEST.matrix
        if cell.engine is EngineType.NATIVE and cell.support is SupportStatus.FALLBACK_REQUIRED
    }

    assert set(NATIVE_BASE_OPERATIONS) == expected_direct
    assert set(NATIVE_ALWAYS_DISCONNECTED) == expected_fallback
    assert native_fallback_supports(EngineType.TRINO, OperationType.OPTIMIZE_MANIFESTS)
    assert not native_fallback_supports(
        EngineType.TRINO,
        OperationType.REWRITE_POSITION_DELETES,
    )
    assert native_fallback_supports(
        EngineType.SPARK_EMR,
        OperationType.REWRITE_POSITION_DELETES,
    )


def test_manifest_evidence_paths_and_symbols_exist() -> None:
    evidence_groups = [
        *(item.evidence for item in CAPABILITY_MANIFEST.engines),
        *(item.evidence for item in CAPABILITY_MANIFEST.catalogs),
        *(item.evidence for item in CAPABILITY_MANIFEST.matrix),
    ]

    for evidence_group in evidence_groups:
        for evidence in evidence_group:
            path = REPO_ROOT / evidence.path
            assert path.is_file(), evidence.path
            assert evidence.symbol in path.read_text(encoding="utf-8"), (
                f"{evidence.path} does not contain {evidence.symbol!r}"
            )


def test_generated_capability_document_is_current() -> None:
    generated = REPO_ROOT / "docs" / "CAPABILITIES.md"
    assert generated.read_text(encoding="utf-8") == render_capabilities_markdown()


def test_capabilities_api_exposes_the_canonical_manifest(api_db) -> None:
    response = api_db.client.get("/api/v1/capabilities", headers=VIEWER_HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == CAPABILITY_MANIFEST.schema_version
    assert len(payload["matrix"]) == len(EngineType) * len(OperationType)
    assert {item["engine"] for item in payload["engines"]} == {
        engine.value for engine in EngineType
    }


def test_capabilities_api_requires_authentication(api_db) -> None:
    response = api_db.client.get("/api/v1/capabilities")

    assert response.status_code == 401
