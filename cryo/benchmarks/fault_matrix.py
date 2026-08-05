from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

EvidenceLevel = Literal["model", "integration", "production"]
FaultStatus = Literal["covered", "partial", "open"]


@dataclass(frozen=True)
class FaultScenario:
    id: str
    dependency: str
    injection: str
    expected_convergence: str
    evidence_level: EvidenceLevel
    status: FaultStatus
    evidence: tuple[str, ...]


CONTROL_PLANE_FAULT_MATRIX: tuple[FaultScenario, ...] = (
    FaultScenario(
        "duplicate-delivery",
        "queue",
        "Deliver one admitted job to two workers.",
        "Exactly one fenced attempt may submit each external effect.",
        "model",
        "covered",
        ("tests/test_durable_intents.py",),
    ),
    FaultScenario(
        "stale-worker",
        "lease",
        "Resume a worker after its lease and table fence are superseded.",
        "The stale attempt is rejected before effect submission.",
        "model",
        "covered",
        ("tests/test_durable_intents.py",),
    ),
    FaultScenario(
        "crash-before-submit",
        "worker",
        "Crash after preparing a receipt but before engine submission.",
        "Reconciliation reclaims the attempt without blind replay.",
        "model",
        "covered",
        ("tests/test_durable_intents.py",),
    ),
    FaultScenario(
        "response-lost-after-effect",
        "engine",
        "Apply the effect but lose the engine response.",
        "Receipt becomes unknown and converges through a destination probe.",
        "model",
        "covered",
        ("tests/test_intent_reconciler.py",),
    ),
    FaultScenario(
        "catalog-commit-failure",
        "catalog",
        "Return a catalog error during Iceberg commit.",
        "The table metadata remains atomic and unchanged.",
        "integration",
        "covered",
        (
            "tests/integration/native/test_native_fault_injection_e2e.py::test_native_c6_catalog_error_is_atomic_no_op",
        ),
    ),
    FaultScenario(
        "object-store-put-failure",
        "object-store",
        "Fail an output data-file PUT.",
        "The chunk aborts without dangling manifest references.",
        "integration",
        "covered",
        (
            "tests/integration/native/test_native_fault_injection_e2e.py::test_native_c7_object_store_put_failure_aborts_chunk",
        ),
    ),
    FaultScenario(
        "postgres-failover",
        "postgres",
        "Interrupt a durable transition transaction.",
        "No acknowledgement precedes commit; retry converges from persisted state.",
        "model",
        "partial",
        (
            "tests/test_control_plane_faults.py::test_interrupted_transition_rolls_back_and_retry_converges",
        ),
    ),
    FaultScenario(
        "redis-loss",
        "redis",
        "Lose queues and leases while jobs are active.",
        "Postgres intent state remains authoritative and work is safely rediscovered.",
        "model",
        "partial",
        ("tests/test_control_plane_faults.py::test_lost_queue_is_rebuilt_from_durable_job_state",),
    ),
    FaultScenario(
        "clock-skew",
        "time",
        "Skew worker and control-plane clocks around expiry and cooldown boundaries.",
        "Database time and monotonic generations prevent stale authorization.",
        "model",
        "partial",
        (
            "tests/test_control_plane_faults.py::test_database_time_controls_expiry_despite_worker_clock_skew",
            "tests/test_control_plane_faults.py::test_database_time_rejects_expired_intent",
        ),
    ),
)


def fault_matrix_report() -> dict[str, object]:
    scenarios = [asdict(scenario) for scenario in CONTROL_PLANE_FAULT_MATRIX]
    return {
        "schema_version": "cryobench.fault-matrix/v1",
        "summary": {
            status: sum(scenario.status == status for scenario in CONTROL_PLANE_FAULT_MATRIX)
            for status in ("covered", "partial", "open")
        },
        "scenarios": scenarios,
    }


def validate_fault_matrix() -> None:
    ids = [scenario.id for scenario in CONTROL_PLANE_FAULT_MATRIX]
    if len(ids) != len(set(ids)):
        raise ValueError("fault scenario IDs must be unique")
    for scenario in CONTROL_PLANE_FAULT_MATRIX:
        if scenario.status == "covered" and not scenario.evidence:
            raise ValueError(f"covered scenario {scenario.id} has no evidence")
        if scenario.status == "open" and scenario.evidence:
            raise ValueError(f"open scenario {scenario.id} must not claim evidence")
