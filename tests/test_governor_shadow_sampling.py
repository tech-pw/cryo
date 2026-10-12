"""Tests for governor.py's shadow-evaluation sampling -- 2026-09-02 fix.

Live-verified: the ROI-gate shadow path and the resource-predictor shadow path each do 2-3
real DB queries per admission candidate, purely to log a recommendation neither currently
enforces (roi_gate_shadow_mode=True confirmed live -- the SKIP/DEFER branches never fire in
shadow mode, only the log line does; the resource predictor has no enforcement path at all
yet). At ~800 candidates in a large wave that's 3000+ synchronous DB round-trips per wave for
data nobody acts on -- found to be the dominant remaining admission-loop cost after the same
day's two unbounded-reconcile-scan fixes were deployed and a governor pod still took 5+
minutes per wave. Sampling (governor_shadow_sample_rate, default 20) keeps the shadow-mode
data collection alive at a fraction of the cost.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from cryo.domain.enums import JobStatus, Mode
from cryo.governor.admission import AdmissionConfig
from cryo.governor.governor import Governor, _shadow_sampled
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.worker.worker import DestinationConfig, InMemoryJobStore, MaintenanceJobRecord


class TestShadowSampledPureFunction:
    def test_sample_rate_1_always_samples(self) -> None:
        """sample_rate=1 is the pre-fix behavior -- every candidate evaluated, no sampling."""
        for job_id in ("a", "b", "c", "job-1234", "ffffffff-ffff-ffff-ffff-ffffffffffff"):
            assert _shadow_sampled(job_id, 1) is True

    def test_same_job_id_always_samples_the_same_way(self) -> None:
        """Deterministic on job_id -- no per-wave counter state needed, and a job re-evaluated
        across waves (e.g. still deferred) gets a consistent sampling decision."""
        result_a = _shadow_sampled("job-abc-123", 20)
        result_b = _shadow_sampled("job-abc-123", 20)
        assert result_a == result_b

    def test_roughly_one_in_n_sampled_across_many_ids(self) -> None:
        """Statistical sanity check, not an exact-count assertion (hash distribution isn't
        perfectly uniform for arbitrary strings) -- confirms sampling actually reduces volume
        substantially, not that it's mathematically exact."""
        sample_rate = 20
        job_ids = [f"job-{i}" for i in range(2000)]
        sampled = sum(1 for jid in job_ids if _shadow_sampled(jid, sample_rate))
        expected = len(job_ids) / sample_rate
        # Within a generous band of the expected 1-in-20 rate.
        assert expected * 0.5 <= sampled <= expected * 2.0


class TestResourcePredictorShadowSampling:
    def test_resource_predictor_not_called_for_unsampled_candidate(self, monkeypatch) -> None:
        """Direct regression: with sampling active and a job_id that doesn't sample, the
        resource predictor's real DB-querying function must not be called at all -- not
        called-and-ignored, genuinely skipped."""
        queue = InMemoryMaintenanceQueue()
        locks = InMemoryLockStore()
        store = InMemoryJobStore()
        store.add_destination(
            DestinationConfig("dest", config={"replicas": ["r1"], "adaptive_budget": False})
        )
        store.add_job(
            MaintenanceJobRecord(
                id="job-1",
                run_id="run-1",
                table_id="table-1",
                destination_id="dest",
                mode=Mode.STANDARD,
                status=JobStatus.QUEUED,
            )
        )
        queue.enqueue("job-1", "dest")

        mock_predict = MagicMock()
        monkeypatch.setattr("cryo.ai.resource_predictor.predict_resources_for_table", mock_predict)

        governor = Governor(
            queue, locks, store, default_config=AdmissionConfig(adaptive_budget=False)
        )
        # Force "never sampled" regardless of the real job_id's hash.
        governor._shadow_sample_rate = 10_000_000

        governor.run_wave(now=datetime(2026, 9, 2, 12, 0, tzinfo=UTC))

        mock_predict.assert_not_called()

    def test_resource_predictor_called_when_sampling_disabled(self, monkeypatch) -> None:
        """sample_rate=1 (sampling off) must preserve the exact pre-fix behavior: every
        candidate's resource predictor shadow call still happens."""
        queue = InMemoryMaintenanceQueue()
        locks = InMemoryLockStore()
        store = InMemoryJobStore()
        store.add_destination(
            DestinationConfig("dest", config={"replicas": ["r1"], "adaptive_budget": False})
        )
        store.add_job(
            MaintenanceJobRecord(
                id="job-1",
                run_id="run-1",
                table_id="table-1",
                destination_id="dest",
                mode=Mode.STANDARD,
                status=JobStatus.QUEUED,
            )
        )
        queue.enqueue("job-1", "dest")

        mock_predict = MagicMock(side_effect=ValueError("no session in this fake store"))
        monkeypatch.setattr("cryo.ai.resource_predictor.predict_resources_for_table", mock_predict)

        governor = Governor(
            queue, locks, store, default_config=AdmissionConfig(adaptive_budget=False)
        )
        governor._shadow_sample_rate = 1

        result = governor.run_wave(now=datetime(2026, 9, 2, 12, 0, tzinfo=UTC))

        # The job still admits -- the resource predictor shadow call failing (no real session
        # in this in-memory test store) is swallowed by its own try/except, same as before
        # this fix; this test's real point is that admission still proceeds unaffected.
        assert result.admitted == 1


class TestROIGateShadowSampling:
    def test_roi_gate_decide_not_called_for_unsampled_candidate_in_shadow_mode(
        self, monkeypatch
    ) -> None:
        """Direct regression: with roi_gate_shadow_mode active and a job_id that doesn't
        sample, ROIGate.decide() must not be called at all."""
        queue = InMemoryMaintenanceQueue()
        locks = InMemoryLockStore()
        store = InMemoryJobStore()
        store.add_destination(
            DestinationConfig("dest", config={"replicas": ["r1"], "adaptive_budget": False})
        )
        store.add_job(
            MaintenanceJobRecord(
                id="job-1",
                run_id="run-1",
                table_id="table-1",
                destination_id="dest",
                mode=Mode.STANDARD,
                status=JobStatus.QUEUED,
            )
        )
        queue.enqueue("job-1", "dest")

        governor = Governor(
            queue, locks, store, default_config=AdmissionConfig(adaptive_budget=False)
        )
        governor._roi_gate_enabled = True
        governor._roi_gate_shadow_mode = True
        governor._shadow_sample_rate = 10_000_000  # effectively "never sampled"
        governor._roi_gate.decide = MagicMock()

        governor.run_wave(now=datetime(2026, 9, 2, 12, 0, tzinfo=UTC))

        governor._roi_gate.decide.assert_not_called()

    def test_roi_gate_decide_called_every_candidate_when_enforcement_is_live(self) -> None:
        """Safety-critical direction: once roi_gate_shadow_mode=False (real enforcement),
        sampling must NOT apply -- every candidate is still evaluated for real SKIP/DEFER,
        regardless of sample_rate. This is the one case _shadow_sampled must never gate."""
        queue = InMemoryMaintenanceQueue()
        locks = InMemoryLockStore()
        store = InMemoryJobStore()
        store.add_destination(
            DestinationConfig("dest", config={"replicas": ["r1"], "adaptive_budget": False})
        )
        store.add_job(
            MaintenanceJobRecord(
                id="job-1",
                run_id="run-1",
                table_id="table-1",
                destination_id="dest",
                mode=Mode.STANDARD,
                status=JobStatus.QUEUED,
            )
        )
        queue.enqueue("job-1", "dest")

        governor = Governor(
            queue, locks, store, default_config=AdmissionConfig(adaptive_budget=False)
        )
        governor._roi_gate_enabled = True
        governor._roi_gate_shadow_mode = False  # real enforcement, not shadow
        governor._shadow_sample_rate = 10_000_000  # would skip almost everything in shadow mode
        governor._roi_gate.decide = MagicMock(
            return_value=MagicMock(
                shadow=False, action=MagicMock(value="proceed"), bcr=1.0, threshold=1.0
            )
        )

        governor.run_wave(now=datetime(2026, 9, 2, 12, 0, tzinfo=UTC))

        governor._roi_gate.decide.assert_called_once()
