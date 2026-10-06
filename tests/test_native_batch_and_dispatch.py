"""Tests for multi-table batch payload and Job create clamps."""

from __future__ import annotations

import base64
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
from cryo.engines.native.batch import (
    main as batch_main,
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


# --- batch.main() payload delivery (2026-08-07 argument-list-too-long fix) --------
#
# Linux caps any single argv/env string at MAX_ARG_STRLEN (128KiB). A large table's
# base64-encoded batch payload can exceed that, and the container fails before Python
# even starts: "exec .../python: argument list too long" -- confirmed live on a
# 211,158-file table. Payload delivery moved to a mounted Secret file
# (CRYO_PAYLOAD_FILE), with CRYO_PAYLOAD_B64 kept as a fallback for any caller still
# using the old path.


def _b64_payload(payload: dict) -> str:
    return base64.b64encode(json.dumps(payload).encode()).decode()


def test_main_reads_payload_from_file(tmp_path, monkeypatch, capsys) -> None:
    payload_file = tmp_path / "payload"
    payload_file.write_text(_b64_payload({"table_ref": "a.b", "operations": []}))
    monkeypatch.setenv("CRYO_PAYLOAD_FILE", str(payload_file))
    monkeypatch.delenv("CRYO_PAYLOAD_B64", raising=False)

    fake_result = SimpleNamespace(ok=True, table_ref="a.b", error=None, results=())
    with patch("cryo.engines.native.batch.execute_request", return_value=fake_result):
        exit_code = batch_main()

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["ok"] is True
    assert out["table_ref"] == "a.b"


def test_main_falls_back_to_env_var_when_no_file(monkeypatch, capsys) -> None:
    monkeypatch.delenv("CRYO_PAYLOAD_FILE", raising=False)
    monkeypatch.setenv("CRYO_PAYLOAD_B64", _b64_payload({"table_ref": "a.b", "operations": []}))

    fake_result = SimpleNamespace(ok=True, table_ref="a.b", error=None, results=())
    with patch("cryo.engines.native.batch.execute_request", return_value=fake_result):
        exit_code = batch_main()

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["table_ref"] == "a.b"


def test_main_prefers_file_over_env_var(tmp_path, monkeypatch, capsys) -> None:
    payload_file = tmp_path / "payload"
    payload_file.write_text(_b64_payload({"table_ref": "from-file", "operations": []}))
    monkeypatch.setenv("CRYO_PAYLOAD_FILE", str(payload_file))
    env_payload = _b64_payload({"table_ref": "from-env", "operations": []})
    monkeypatch.setenv("CRYO_PAYLOAD_B64", env_payload)

    fake_result = SimpleNamespace(ok=True, table_ref="from-file", error=None, results=())
    with patch("cryo.engines.native.batch.execute_request", return_value=fake_result) as mock_exec:
        batch_main()

    assert mock_exec.call_args.args[0]["table_ref"] == "from-file"


def test_main_errors_cleanly_when_payload_file_unreadable(monkeypatch, capsys) -> None:
    monkeypatch.setenv("CRYO_PAYLOAD_FILE", "/nonexistent/path/payload")
    monkeypatch.delenv("CRYO_PAYLOAD_B64", raising=False)

    exit_code = batch_main()

    assert exit_code == 1
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is False
    assert "CRYO_PAYLOAD_FILE" in out["error"]


def test_main_errors_when_neither_file_nor_env_set(monkeypatch, capsys) -> None:
    monkeypatch.delenv("CRYO_PAYLOAD_FILE", raising=False)
    monkeypatch.delenv("CRYO_PAYLOAD_B64", raising=False)

    exit_code = batch_main()

    assert exit_code == 1
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is False


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


def test_execute_batch_reports_member_count_and_peak_cgroup_memory() -> None:
    """Cost remediation plan §3, 2026-09-16 (docs/CRYO_UNUSED_COST_FIX_2026-09-16.md): the
    aggregate result must carry member_count and peak_cgroup_memory_mib so real batch memory
    telemetry can accumulate (see _log_batch_memory_telemetry's docstring in watcher.py) --
    the exact measurement packing.py's own _SERIAL_MEMBER_OVERHEAD_MIB comment says is missing
    before that heuristic can be safely revisited."""
    from cryo.engines.native.runner import NativeExecuteResult

    def fake_execute(request, config=None, timeout_seconds=None, on_progress=None):
        return NativeExecuteResult(ok=True, table_ref=str(request.get("table_ref")), results=())

    payload = build_batch_payload(
        [{"table_ref": "a.b", "job_id": "j1", "operations": []}]
    )
    with (
        patch("cryo.engines.native.batch.execute_request", side_effect=fake_execute),
        patch("cryo.engines.native.batch.read_peak_cgroup_memory_mib", return_value=512),
    ):
        result = execute_batch(payload, config={})

    assert result["member_count"] == 1
    assert result["peak_cgroup_memory_mib"] == 512


def test_read_peak_cgroup_memory_mib_reads_cgroup_v2_file(tmp_path, monkeypatch) -> None:
    import builtins

    from cryo.engines.native.batch import read_peak_cgroup_memory_mib

    real_open = builtins.open
    v2_path = tmp_path / "memory.peak"
    v2_path.write_text(str(512 * 1024 * 1024))

    def fake_open(path, *args, **kwargs):
        if path == "/sys/fs/cgroup/memory.peak":
            return real_open(v2_path, *args, **kwargs)
        raise OSError("not found")

    monkeypatch.setattr("builtins.open", fake_open)
    assert read_peak_cgroup_memory_mib() == 512


def test_read_peak_cgroup_memory_mib_falls_back_to_cgroup_v1(tmp_path, monkeypatch) -> None:
    import builtins

    from cryo.engines.native.batch import read_peak_cgroup_memory_mib

    real_open = builtins.open
    v1_path = tmp_path / "memory.max_usage_in_bytes"
    v1_path.write_text(str(1024 * 1024 * 1024))

    def fake_open(path, *args, **kwargs):
        if path == "/sys/fs/cgroup/memory/memory.max_usage_in_bytes":
            return real_open(v1_path, *args, **kwargs)
        raise OSError("not found")

    monkeypatch.setattr("builtins.open", fake_open)
    assert read_peak_cgroup_memory_mib() == 1024


def test_read_peak_cgroup_memory_mib_returns_none_not_zero_when_unreadable(monkeypatch) -> None:
    """Neither cgroup file exists (e.g. local dev, not inside a real cgroup) -- must return
    None, not 0, since 0 would misleadingly look like a real measurement of zero usage."""
    from cryo.engines.native.batch import read_peak_cgroup_memory_mib

    def fake_open(path, *args, **kwargs):
        raise OSError("not found")

    monkeypatch.setattr("builtins.open", fake_open)
    assert read_peak_cgroup_memory_mib() is None


def test_log_batch_memory_telemetry_logs_at_info_with_the_measured_values() -> None:
    from cryo.engines.native.watcher import _log_batch_memory_telemetry

    dispatch_id = uuid.uuid4()
    stdout = json.dumps(
        {"batch": True, "ok": True, "member_count": 7, "peak_cgroup_memory_mib": 3200}
    )
    with patch("cryo.engines.native.watcher.logger.info") as mock_info:
        _log_batch_memory_telemetry(dispatch_id, stdout)

    assert mock_info.called
    args = mock_info.call_args.args
    assert 7 in args
    assert 3200 in args


def test_log_batch_memory_telemetry_is_a_noop_when_measurement_missing() -> None:
    """A batch that ran without the cgroup file available (peak_cgroup_memory_mib is None,
    e.g. local dev) must not log a misleading line implying a real measurement exists."""
    from cryo.engines.native.watcher import _log_batch_memory_telemetry

    stdout = json.dumps({"batch": True, "ok": True, "member_count": 1, "peak_cgroup_memory_mib": None})
    with patch("cryo.engines.native.watcher.logger.info") as mock_info:
        _log_batch_memory_telemetry(uuid.uuid4(), stdout)

    assert not mock_info.called


def test_execute_batch_circuit_breaker_opens_after_repeated_same_catalog_timeout() -> None:
    """Cost remediation plan §1, 2026-09-16 (docs/CRYO_UNUSED_COST_FIX_2026-09-16.md): a real
    incident found a native pod spending ~48+ minutes re-discovering the same unreachable
    metastore across 32 serial tables, each paying its own bounded connect-timeout before
    moving on. After _CIRCUIT_BREAKER_CONSECUTIVE_FAILURES (2) consecutive transient-
    metastore-timeout failures against the same catalog_uri, the breaker must open and every
    remaining member sharing that URI must be short-circuited without calling execute_request
    at all -- proving the pod stops burning reserved capacity on a known-dead catalog."""
    from cryo.engines.native.batch import TRANSIENT_METASTORE_ERROR_SUBSTRING

    calls: list[str] = []
    timeout_error = (
        "failed to connect to catalog: after 1 attempts: failed to create Hive client: "
        "failed to connect to metastore: connect timed out after 45s"
    )

    def fake_execute(request, config=None, timeout_seconds=None, on_progress=None):
        from cryo.engines.native.runner import NativeExecuteResult

        ref = str(request.get("table_ref"))
        calls.append(ref)
        return NativeExecuteResult(ok=False, table_ref=ref, results=(), error=timeout_error)

    payload = build_batch_payload(
        [
            {
                "table_ref": f"t{i}",
                "job_id": f"j{i}",
                "operations": [],
                "catalog_uri": "thrift://dead-metastore:9083",
            }
            for i in range(5)
        ]
    )
    with patch("cryo.engines.native.batch.execute_request", side_effect=fake_execute):
        result = execute_batch(payload, config={})

    # Only the first 2 members actually spawned the subprocess -- the breaker opened after
    # that and the remaining 3 were short-circuited.
    assert calls == ["t0", "t1"]
    assert len(result["results"]) == 5
    for entry in result["results"][2:]:
        assert entry["ok"] is False
        assert TRANSIENT_METASTORE_ERROR_SUBSTRING in entry["error"]
        assert "catalog_circuit_breaker_open" in entry["error"]


def test_execute_batch_circuit_breaker_does_not_open_for_unrelated_failures() -> None:
    """The breaker must only key on the specific transient-metastore-timeout signature -- a
    run of ordinary per-table failures (bad credentials, missing table, etc.) against the same
    catalog_uri must not trip it and start skipping real, distinguishable failures."""
    calls: list[str] = []

    def fake_execute(request, config=None, timeout_seconds=None, on_progress=None):
        from cryo.engines.native.runner import NativeExecuteResult

        ref = str(request.get("table_ref"))
        calls.append(ref)
        return NativeExecuteResult(ok=False, table_ref=ref, results=(), error="table not found")

    payload = build_batch_payload(
        [
            {
                "table_ref": f"t{i}",
                "job_id": f"j{i}",
                "operations": [],
                "catalog_uri": "thrift://healthy-metastore:9083",
            }
            for i in range(5)
        ]
    )
    with patch("cryo.engines.native.batch.execute_request", side_effect=fake_execute):
        execute_batch(payload, config={})

    # Every member actually ran -- the breaker never opened for a non-timeout error.
    assert calls == ["t0", "t1", "t2", "t3", "t4"]


def test_execute_batch_circuit_breaker_is_scoped_per_catalog_uri() -> None:
    """A dead catalog A must not affect dispatch to a healthy, unrelated catalog B in the same
    batch -- confirmed live requirement (cost doc §1: 'stop new work for the failing catalog...
    while keeping unrelated catalogs available')."""
    calls: list[str] = []
    timeout_error = (
        "failed to connect to catalog: after 1 attempts: failed to create Hive client: "
        "failed to connect to metastore: connect timed out after 45s"
    )

    def fake_execute(request, config=None, timeout_seconds=None, on_progress=None):
        from cryo.engines.native.runner import NativeExecuteResult

        ref = str(request.get("table_ref"))
        calls.append(ref)
        if request.get("catalog_uri") == "thrift://dead-metastore:9083":
            return NativeExecuteResult(ok=False, table_ref=ref, results=(), error=timeout_error)
        return NativeExecuteResult(ok=True, table_ref=ref, results=())

    payload = build_batch_payload(
        [
            {"table_ref": "dead1", "job_id": "j1", "operations": [], "catalog_uri": "thrift://dead-metastore:9083"},
            {"table_ref": "dead2", "job_id": "j2", "operations": [], "catalog_uri": "thrift://dead-metastore:9083"},
            {"table_ref": "healthy1", "job_id": "j3", "operations": [], "catalog_uri": "thrift://healthy-metastore:9083"},
            {"table_ref": "dead3", "job_id": "j4", "operations": [], "catalog_uri": "thrift://dead-metastore:9083"},
        ]
    )
    with patch("cryo.engines.native.batch.execute_request", side_effect=fake_execute):
        result = execute_batch(payload, config={})

    # dead1, dead2 spawned (opening the breaker for that URI); healthy1 spawned regardless
    # (different URI); dead3 was short-circuited (breaker already open for its URI).
    assert calls == ["dead1", "dead2", "healthy1"]
    assert result["results"][2]["ok"] is True  # healthy1 unaffected
    assert result["results"][3]["ok"] is False
    assert "catalog_circuit_breaker_open" in result["results"][3]["error"]


def test_execute_batch_circuit_breaker_resets_on_intervening_success() -> None:
    """A single transient timeout followed by a real success against the same catalog must
    not count toward the breaker -- only a genuine consecutive run of the same failure opens
    it, per _CIRCUIT_BREAKER_CONSECUTIVE_FAILURES' docstring."""
    calls: list[str] = []
    timeout_error = (
        "failed to connect to catalog: after 1 attempts: failed to create Hive client: "
        "failed to connect to metastore: connect timed out after 45s"
    )
    outcomes = [False, True, False, False]

    def fake_execute(request, config=None, timeout_seconds=None, on_progress=None):
        from cryo.engines.native.runner import NativeExecuteResult

        ref = str(request.get("table_ref"))
        calls.append(ref)
        ok = outcomes[len(calls) - 1]
        return NativeExecuteResult(
            ok=ok, table_ref=ref, results=(), error=None if ok else timeout_error
        )

    payload = build_batch_payload(
        [
            {"table_ref": f"t{i}", "job_id": f"j{i}", "operations": [], "catalog_uri": "thrift://flaky:9083"}
            for i in range(5)
        ]
    )
    with patch("cryo.engines.native.batch.execute_request", side_effect=fake_execute):
        execute_batch(payload, config={})

    # t0 fails (count=1), t1 succeeds (resets to 0), t2 fails (count=1), t3 fails (count=2,
    # breaker opens) -- t4 must be short-circuited, but all of t0-t3 actually ran.
    assert calls == ["t0", "t1", "t2", "t3"]


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
    pod_spec = manifest["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    # Payload is delivered via a mounted Secret file, not an env var -- Linux caps any
    # single argv/env string at MAX_ARG_STRLEN (128KiB), which a large table's payload
    # can exceed as base64 (confirmed live, 2026-08-07: "argument list too long" on a
    # 211,158-file table). The env var only carries the file path now.
    payload_file_env = next(e for e in container["env"] if e["name"] == "CRYO_PAYLOAD_FILE")
    assert payload_file_env["value"] == "/etc/cryo/payload/payload"
    assert not any("valueFrom" in e for e in container["env"])
    volume_mount = next(v for v in container["volumeMounts"] if v["name"] == "payload")
    assert volume_mount["mountPath"] == "/etc/cryo/payload"
    volume = next(v for v in pod_spec["volumes"] if v["name"] == "payload")
    assert volume["secret"]["secretName"] == "cryo-native-test-payload"
    core_api.patch_namespaced_secret.assert_called_once()
    pod_labels = manifest["spec"]["template"]["metadata"]["labels"]
    assert pod_labels["eks-workload-name"] == "cryo-native"
    assert pod_labels["eks-workload-type"] == "Job"
    assert pod_labels["cryo-engine"] == "native"
    assert pod_labels["cryo-operation"] == "maintenance"
    assert manifest["metadata"]["labels"]["eks-workload-name"] == "cryo-native"


def test_create_native_job_labels_operation_from_payload() -> None:
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
            {"operations": [{"type": "rewrite_data_files"}], "table_ref": "a.b"},
            config={"k8s_namespace": "cryo"},
            cpu_milli=500,
            mem_mib=1024,
            job_name="cryo-native-opt",
        )
    assert result.ok
    labels = batch_api.create_namespaced_job.call_args.kwargs["body"]["spec"]["template"][
        "metadata"
    ]["labels"]
    assert labels["cryo-operation"] == "rewrite_data_files"
    assert labels["cryo-engine"] == "native"


def test_read_pod_logs_does_not_limit_bytes() -> None:
    """Regression: `limit_bytes` truncates a pod log from the START, not the end.

    Same bug class already fixed in cryo/engines/spark/k8s.py::_read_pod_log_tail
    (2026-07-31, see its docstring) but left unfixed here, where it's worse: a shared
    batch pod can carry up to 32 tables' worth of CRYO_TABLE_RESULT lines
    (packing.py max_tables_per_pod=32), and any line past the old 512KiB cutoff was
    silently dropped, making NativeJobWatcher._finalize_success report
    "native_job_no_result" for tables whose batch Job actually succeeded.
    """
    from cryo.engines.native.job_dispatch import _read_pod_logs

    core_api = MagicMock()
    core_api.read_namespaced_pod_log.return_value = "full log contents"
    pod = SimpleNamespace(metadata=SimpleNamespace(name="p1", namespace="cryo"))

    with patch("kubernetes.client.CoreV1Api", return_value=core_api):
        result = _read_pod_logs([pod], "cryo-engine", object())

    assert result == "full log contents"
    call_kwargs = core_api.read_namespaced_pod_log.call_args.kwargs
    assert "limit_bytes" not in call_kwargs


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
