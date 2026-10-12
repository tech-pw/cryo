"""Regression test for the admission/lifecycle redesign, 2026-09-16.

Real incident, 2026-09-15/16: cryo/governor/native_admit.py (the sole source of the
"metastore_capacity:"/"native_cluster_capacity"/"fleet_admission_rate_cap" defer-reason strings)
and cryo/db/repositories/run_finalizer.py (the sole consumer, deciding how long a run stays open
once a job is DEFERRED) each independently declared their own copy of "which defer reasons mean
fleet-wide capacity vs. job-specific." Those two copies happened to agree at the time, but nothing
enforced that -- and that's exactly the kind of drift that let a 10-minute grace built for
job-specific reasons get silently applied to capacity defers too, mass-failing 29,832 jobs.

Both modules now import the classification from cryo.domain.enums instead of each declaring
their own copy. This test doesn't re-test the grace-window behavior (test_run_finalizer.py
already does) -- it specifically pins the *shared-source* property: every literal reason string
native_admit.py actually dispatches with is_capacity_defer_reason()-classified as capacity, using
the exact same function run_finalizer.py calls, so a future edit to native_admit.py's reason
strings that isn't also reflected in the shared enum will fail this test immediately instead of
silently reintroducing the drift.
"""

from __future__ import annotations

from cryo.domain.enums import (
    CAPACITY_DEFER_REASON_PREFIXES,
    FLEET_ADMISSION_RATE_CAP_DEFER_REASON,
    METASTORE_CAPACITY_DEFER_REASON_PREFIX,
    NATIVE_CLUSTER_CAPACITY_DEFER_REASON,
    is_capacity_defer_reason,
)


def test_native_admit_reason_constants_are_all_classified_as_capacity() -> None:
    assert is_capacity_defer_reason(NATIVE_CLUSTER_CAPACITY_DEFER_REASON)
    assert is_capacity_defer_reason(FLEET_ADMISSION_RATE_CAP_DEFER_REASON)
    assert is_capacity_defer_reason(f"{METASTORE_CAPACITY_DEFER_REASON_PREFIX}thrift://x:9083")


def test_native_admit_py_imports_the_shared_constants_not_local_literals() -> None:
    """Guards against someone reverting native_admit.py's call sites back to hardcoded string
    literals (which would silently decouple it from this shared classification again) -- checks
    the actual names bound in that module's namespace after import."""
    import cryo.governor.native_admit as native_admit

    assert native_admit.NATIVE_CLUSTER_CAPACITY_DEFER_REASON == NATIVE_CLUSTER_CAPACITY_DEFER_REASON
    assert (
        native_admit.FLEET_ADMISSION_RATE_CAP_DEFER_REASON == FLEET_ADMISSION_RATE_CAP_DEFER_REASON
    )
    assert (
        native_admit.METASTORE_CAPACITY_DEFER_REASON_PREFIX
        == METASTORE_CAPACITY_DEFER_REASON_PREFIX
    )


def test_run_finalizer_py_imports_the_shared_classifier_not_a_local_copy() -> None:
    import cryo.db.repositories.run_finalizer as run_finalizer

    assert run_finalizer.is_capacity_defer_reason is is_capacity_defer_reason
    # The old module-local duplicate must be gone, not just unused.
    assert not hasattr(run_finalizer, "_CAPACITY_DEFER_REASON_PREFIXES")
    assert not hasattr(run_finalizer, "_is_capacity_defer_reason")


def test_a_new_unclassified_reason_string_does_not_accidentally_match() -> None:
    """Baseline: a genuinely job-specific defer reason (not capacity-related) must not match --
    otherwise this whole classification is a no-op that grants every defer the long grace."""
    assert not is_capacity_defer_reason("native_admit_plan_rebuild_failed")
    assert not is_capacity_defer_reason("k8s_job_create_failed")
    assert not is_capacity_defer_reason(None)
    assert not is_capacity_defer_reason("")


def test_capacity_defer_reason_prefixes_tuple_matches_the_named_constants() -> None:
    """The bulk tuple (used for the startswith check) must actually contain the same three
    named constants callers reference individually -- catches someone updating one but not
    the other."""
    assert set(CAPACITY_DEFER_REASON_PREFIXES) == {
        METASTORE_CAPACITY_DEFER_REASON_PREFIX,
        NATIVE_CLUSTER_CAPACITY_DEFER_REASON,
        FLEET_ADMISSION_RATE_CAP_DEFER_REASON,
    }
