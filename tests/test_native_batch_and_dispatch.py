"""Tests for multi-table batch payload and Job create clamps."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cryo.domain.enums import JobStatus
from cryo.engines.native.batch import (
    TABLE_RESULT_PREFIX,
    build_batch_payload,
    execute_batch,
    is_batch_payload,
)
from cryo.engines.native.job_dispatch import create_native_job
from cryo.engines.native.resources import MAX_POD_MEM_MIB
from cryo.engines.native.watcher import NativeJobWatcher, _parse_batch_results
from cryo.queue.locks import InMemoryLockStore


def test_build_batch_payload() -> None:
    payload = build_batch_payload(
        [{"table_ref": "a.b", "operations": []}, {"table_ref": "a.c", "operations": []}],
        dispatch_id="d1",
        run_id="r1",
    )
    assert payload["batch"] is True
    assert len(payload["tables"]) == 2
    assert is_batch_payload(payload)


def test_execute_batch_serial_isolation() -> None:
    calls: list[str] = []

    def fake_execute(request, config=None, timeout_seconds=None, on_progress=None):
        from cryo.engines.native.runner import NativeExecuteResult

        ref = str(request.get("table_ref"))
        calls.append(ref)
        ok = ref != "fail.me"
        return NativeExecuteResult(
            ok=ok,
            table_ref=ref,
            results=(),
            error=None if ok else "boom",
        )

    payload = build_batch_payload(
        [
            {"table_ref": "ok.one", "job_id": "j1", "operations": []},
            {"table_ref": "fail.me", "job_id": "j2", "operations": []},
            {"table_ref": "ok.two", "job_id": "j3", "operations": []},
        ]
    )
    with patch("cryo.engines.native.batch.execute_request", side_effect=fake_execute):
        result = execute_batch(payload, config={})
    assert calls == ["ok.one", "fail.me", "ok.two"]
    assert result["ok"] is False
    assert len(result["results"]) == 3
    assert result["results"][0]["ok"] is True
    assert result["results"][1]["ok"] is False
    assert result["results"][2]["ok"] is True


def test_create_native_job_clamps_above_16gi() -> None:
    with (
        patch("cryo.engines.native.job_dispatch._safe_load_k8s", return_value=(None, None)),
    ):
        result = create_native_job(
            {"batch": True, "tables": []},
            config={},
            cpu_milli=99999,
            mem_mib=99999,
        )
    assert result.mem_mib <= MAX_POD_MEM_MIB
    assert result.cpu_milli <= 4000
    assert result.ok is False


def test_create_native_job_uses_payload_secret() -> None:
    batch_api = MagicMock()
    batch_api.create_namespaced_job.return_value = SimpleNamespace(
        metadata=SimpleNamespace(uid="job-uid")
    )
    core_api = MagicMock()
    with (
        patch("cryo.engines.native.job_dispatch._safe_load_k8s", return_value=(object(), object())),
        patch("cryo.engines.native.job_dispatch._in_cluster_configured", return_value=True),
        patch("cryo.engines.native.job_dispatch._new_k8s_batch_client", return_value=batch_api),
        patch("cryo.engines.native.job_dispatch._new_k8s_core_client", return_value=core_api),
    ):
        result = create_native_job(
            {"batch": True, "tables": [{"table_ref": "a.b"}]},
            config={"k8s_namespace": "cryo"},
            cpu_milli=500,
            mem_mib=1024,
            job_name="cryo-native-test",
        )

    assert result.ok
    secret = core_api.create_namespaced_secret.call_args.kwargs["body"]
    assert secret["stringData"]["payload"]
    manifest = batch_api.create_namespaced_job.call_args.kwargs["body"]
    payload_env = manifest["spec"]["template"]["spec"]["containers"][0]["env"][0]
    assert payload_env["valueFrom"]["secretKeyRef"]["name"] == "cryo-native-test-payload"
    core_api.patch_namespaced_secret.assert_called_once()


def test_parse_batch_results() -> None:
    stdout = (
        'progress\n{"ok": false, "batch": true, "results": ['
        '{"ok": true, "job_id": "j1"}, {"ok": false, "job_id": "j2", "error": "x"}]}'
    )
    parsed = _parse_batch_results(stdout)
    assert parsed["j1"]["ok"] is True
    assert parsed["j2"]["ok"] is False


def test_parse_batch_results_recovers_streamed_members_without_final_json() -> None:
    stdout = (
        f'{TABLE_RESULT_PREFIX}{{"ok":true,"job_id":"j1","results":[]}}\n'
        "process terminated by OOMKilled"
    )

    parsed = _parse_batch_results(stdout)

    assert parsed == {"j1": {"ok": True, "job_id": "j1", "results": []}}


def test_watcher_preserves_partial_batch_success_and_audits() -> None:
    jobs = [
        SimpleNamespace(
            id=uuid.uuid4(),
            table_id=uuid.uuid4(),
            status=JobStatus.RUNNING.value,
            plan={"operations": [{"type": "optimize"}]},
        ),
        SimpleNamespace(
            id=uuid.uuid4(),
            table_id=uuid.uuid4(),
            status=JobStatus.RUNNING.value,
            plan={"operations": [{"type": "optimize"}]},
        ),
    ]
    locks = InMemoryLockStore()
    for job in jobs:
        assert locks.acquire_table_lock(str(job.table_id), str(job.id), 3600)
    statuses: list[tuple[str, JobStatus, str | None]] = []
    audits = []
    watcher = NativeJobWatcher(
        session_factory=MagicMock(),
        lock_store=locks,
        mark_status=lambda job_id, status, error=None, **_: statuses.append(
            (job_id, status, error)
        ),
        record_audit=audits.append,
    )
    stdout = (
        '{"ok":false,"batch":true,"results":['
        f'{{"job_id":"{jobs[0].id}","ok":true,"results":['
        '{"operation":"rewrite_data_files","success":true,"input_files":3,"output_files":1}]},'
        f'{{"job_id":"{jobs[1].id}","ok":false,"error":"boom","results":['
        '{"operation":"rewrite_data_files","success":false,"error":"boom"}]}]}'
    )
    dispatch_id = uuid.uuid4()
    with (
        patch("cryo.engines.native.watcher.jobs_for_dispatch", return_value=jobs),
        patch("cryo.engines.native.watcher.mark_dispatch_finished") as finish,
    ):
        watcher._finalize_success(MagicMock(), dispatch_id, stdout)

    assert statuses == [
        (str(jobs[0].id), JobStatus.SUCCEEDED, None),
        (str(jobs[1].id), JobStatus.FAILED, "boom"),
    ]
    assert [audit.status for audit in audits] == ["OK", "FAILED"]
    assert not locks.is_table_locked(str(jobs[0].table_id))
    assert not locks.is_table_locked(str(jobs[1].table_id))
    assert finish.call_args.kwargs["status"] == "partial"


def test_line_to_log_events_emits_one_event_per_operation_in_table_result() -> None:
    """A single CRYO_TABLE_RESULT line bundling two operations for one table must
    produce two events -- otherwise the second operation silently never appears in
    the live log (regression: an earlier draft only handled the first result)."""
    from cryo.engines.native.watcher import _line_to_log_events

    line = TABLE_RESULT_PREFIX + json.dumps(
        {
            "ok": True,
            "job_id": "j1",
            "table_id": "t1",
            "table_ref": "ns.tbl",
            "results": [
                {"operation": "expire_snapshots", "success": True, "duration_ms": 101},
                {"operation": "remove_orphan_files", "success": True, "duration_ms": 42},
            ],
        }
    )
    events = _line_to_log_events(line, run_id="r1")
    assert [e["operation"] for e in events] == ["expire_snapshots", "remove_orphan_files"]
    assert all(e["job_id"] == "j1" and e["table_id"] == "t1" for e in events)
    assert all(e["level"] == "info" for e in events)
    assert "101ms" in events[0]["message"]


def test_line_to_log_events_marks_failed_operation_as_error_level() -> None:
    from cryo.engines.native.watcher import _line_to_log_events

    line = TABLE_RESULT_PREFIX + json.dumps(
        {
            "ok": False,
            "job_id": "j1",
            "table_id": "t1",
            "table_ref": "ns.tbl",
            "results": [
                {"operation": "optimize", "success": False, "error": "boom"},
            ],
        }
    )
    events = _line_to_log_events(line, run_id="r1")
    assert len(events) == 1
    assert events[0]["level"] == "error"
    assert "boom" in events[0]["message"]


def test_line_to_log_events_handles_table_level_failure_with_no_results() -> None:
    from cryo.engines.native.watcher import _line_to_log_events

    line = TABLE_RESULT_PREFIX + json.dumps(
        {"ok": False, "job_id": "j1", "table_id": "t1", "error": "plan build failed", "results": []}
    )
    events = _line_to_log_events(line, run_id="r1")
    assert len(events) == 1
    assert events[0]["level"] == "error"
    assert "plan build failed" in events[0]["message"]


def test_line_to_log_events_parses_progress_prefixed_line() -> None:
    from cryo.engines.native.runner import PROGRESS_PREFIX
    from cryo.engines.native.watcher import _line_to_log_events

    line = PROGRESS_PREFIX + json.dumps(
        {"operation": "optimize", "event": "progress", "progress_pct": 40}
    )
    events = _line_to_log_events(line, run_id="r1")
    assert len(events) == 1
    assert events[0]["operation"] == "optimize"
    assert "40%" in events[0]["message"]


def test_line_to_log_events_ignores_unrecognized_lines() -> None:
    from cryo.engines.native.watcher import _line_to_log_events

    assert _line_to_log_events("some unrelated log noise", run_id="r1") == []
    assert _line_to_log_events(TABLE_RESULT_PREFIX + "not json", run_id="r1") == []


def test_publish_incremental_progress_dedupes_across_ticks() -> None:
    """Same dispatch, same log content on two consecutive ticks -- the second tick
    must publish nothing new, or the UI would show every line duplicated forever."""
    from cryo.engines.native.watcher import _INCREMENTAL_LOG_SEEN

    dispatch = SimpleNamespace(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        k8s_job_name="cryo-native-abc123",
    )
    _INCREMENTAL_LOG_SEEN.pop(str(dispatch.id), None)
    stdout = TABLE_RESULT_PREFIX + json.dumps(
        {
            "ok": True,
            "job_id": "j1",
            "table_id": "t1",
            "table_ref": "ns.tbl",
            "results": [{"operation": "expire_snapshots", "success": True}],
        }
    )
    published: list[dict] = []
    watcher = NativeJobWatcher(
        session_factory=MagicMock(),
        lock_store=InMemoryLockStore(),
        mark_status=lambda *a, **k: None,
        run_events=MagicMock(publish=lambda run_id, event: published.append(event)),
    )
    with (
        patch("cryo.engines.native.watcher._get_job_pods_by_selector", return_value=[object()]),
        patch("cryo.engines.native.watcher._read_recent_pod_logs", return_value=stdout),
    ):
        watcher._publish_incremental_progress(MagicMock(), dispatch, "cryo")
        watcher._publish_incremental_progress(MagicMock(), dispatch, "cryo")

    assert len(published) == 1
    _INCREMENTAL_LOG_SEEN.pop(str(dispatch.id), None)
