"""Tests for write-ahead job step ledger."""

from __future__ import annotations

from cryo.db.repositories.job_steps import InMemoryJobStepStore
from cryo.domain.enums import JobStepStatus, OperationType


def test_begin_step_creates_started() -> None:
    store = InMemoryJobStepStore()
    step = store.begin_step("job-1", 0, OperationType.EXPIRE_SNAPSHOTS, detail="SQL")
    assert step.status is JobStepStatus.STARTED
    assert step.step_index == 0
    assert store.get_started_step("job-1") is not None


def test_begin_step_idempotent_when_already_started() -> None:
    store = InMemoryJobStepStore()
    first = store.begin_step("job-1", 0, OperationType.OPTIMIZE, detail="a")
    second = store.begin_step("job-1", 0, OperationType.OPTIMIZE, detail="b")
    assert first.id == second.id
    assert second.status is JobStepStatus.STARTED


def test_complete_step_sets_finished() -> None:
    store = InMemoryJobStepStore()
    store.begin_step("job-1", 0, OperationType.OPTIMIZE, detail="q1")
    done = store.complete_step("job-1", 0, JobStepStatus.SUCCEEDED)
    assert done is not None
    assert done.status is JobStepStatus.SUCCEEDED
    assert done.finished_at is not None
    assert store.get_started_step("job-1") is None


def test_list_steps_ordered() -> None:
    store = InMemoryJobStepStore()
    store.begin_step("job-1", 1, OperationType.OPTIMIZE)
    store.complete_step("job-1", 1, JobStepStatus.SUCCEEDED)
    store.begin_step("job-1", 0, OperationType.EXPIRE_SNAPSHOTS)
    steps = store.list_steps("job-1")
    assert [s.step_index for s in steps] == [0, 1]
