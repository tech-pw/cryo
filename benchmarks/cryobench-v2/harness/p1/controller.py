"""CryoBench V2 phase 1 controller (runs in-cluster as bench-v2-controller).

One trial = generate the fleet for all arms, measure the baseline, then CYCLES cycles of:
ingest (identical for all arms) -> maintenance windows A1, A2, A3 in rotated order, one at a
time -> Trino measurement of all arms. A0 never gets maintenance.

Child Job logs are copied into this controller's log as `CHILD <job> <line>` so the raw
evidence survives pod garbage collection. Controller events are `CTRL {...}` lines.
"""
import json, os, sys, time, urllib.error, urllib.request
from kubernetes import client, config

NS = "cryo-bench"
SPARK_IMAGE = "ghcr.io/tech-pw/cryo-spark@sha256:1114326529fbae0e8038f674c232230076ce670ab04786d331594519ccb8e07e"
CRYO_IMAGE = "ghcr.io/tech-pw/cryo@sha256:b440406dca311463c3ba672a8b6b6d469442f23851681662984effe82df1c691"
API = f"http://cryo-bench-api.{NS}.svc:8000/api/v1"
SOURCE_ID = "91e8f769-cffe-4c89-877d-a3aedcbb4df4"
TRIALS = [int(x) for x in os.environ.get("TRIALS", "1").split(",")]
CYCLES = int(os.environ.get("CYCLES", "4"))
ROTATIONS = [["a1", "a2", "a3"], ["a2", "a3", "a1"], ["a3", "a1", "a2"]]
TABLES = [f"{c}_{i}" for c in ("healthy", "moderate", "severe", "updates", "lowtraffic") for i in (1, 2)]
CLASS_INDEX = {"healthy": 0, "moderate": 1, "severe": 2, "updates": 3, "lowtraffic": 4}
TERMINAL_JOB = {"succeeded", "failed", "skipped", "partial", "cancelled", "deferred"}

config.load_incluster_config()
batch = client.BatchV1Api()
core = client.CoreV1Api()
apps = client.AppsV1Api()


def emit(**kw):
    kw.update(utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), t=round(time.time(), 3))
    print("CTRL " + json.dumps(kw, default=str), flush=True)


# ---------------------------------------------------------------- Kubernetes jobs
def s3_env():
    return [
        {"name": "AWS_ACCESS_KEY_ID", "valueFrom": {"secretKeyRef": {"name": "cryo-bench-s3", "key": "access_key_id"}}},
        {"name": "AWS_SECRET_ACCESS_KEY", "valueFrom": {"secretKeyRef": {"name": "cryo-bench-s3", "key": "secret_access_key"}}},
        {"name": "AWS_REGION", "value": "<region>"},
    ]


def spark_job(name, env, cpu, mem_gi, cores, script="spark_p1.py", deadline=7200):
    return {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": name, "namespace": NS,
                     "labels": {"benchmark-run": "20261020-spark-001", "benchmark-version": "v2", "purpose": "v2p1"}},
        "spec": {"backoffLimit": 0, "activeDeadlineSeconds": deadline, "ttlSecondsAfterFinished": 604800,
                 "template": {"metadata": {"labels": {"benchmark-version": "v2", "v2-job": name},
                                           "annotations": {"karpenter.sh/do-not-disrupt": "true"}},
                              "spec": {"restartPolicy": "Never", "serviceAccountName": "bench-spark-jobs",
                                       "nodeSelector": {"kubernetes.io/arch": "amd64"},
                                       "containers": [{
                                           "name": "spark", "image": SPARK_IMAGE,
                                           "command": ["/opt/spark/bin/spark-submit", "--master", f"local[{cores}]",
                                                       "--driver-memory", f"{int(mem_gi * 0.75)}g", f"/opt/v2/{script}"],
                                           "env": s3_env() + [{"name": k, "value": str(v)} for k, v in env.items()],
                                           "volumeMounts": [{"name": "code", "mountPath": "/opt/v2"}],
                                           "resources": {"requests": {"cpu": str(cpu), "memory": f"{mem_gi}Gi"},
                                                         "limits": {"cpu": str(cpu), "memory": f"{mem_gi}Gi"}}}],
                                       "volumes": [{"name": "code", "configMap": {"name": "v2-p1-code"}}]}}},
    }


def measure_job(name, phase, targets, qset="q_agg,q_filter,q_point", extra_env=None, reps=3, trino="bench-trino"):
    return {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": name, "namespace": NS,
                     "labels": {"benchmark-run": "20261020-spark-001", "benchmark-version": "v2", "purpose": "v2p1-measure"}},
        "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 21600, "ttlSecondsAfterFinished": 604800,
                 "template": {"metadata": {"annotations": {"karpenter.sh/do-not-disrupt": "true"}, "labels": {"benchmark-version": "v2", "v2-job": name}},
                              "spec": {"restartPolicy": "Never", "serviceAccountName": "bench-spark-jobs",
                                       "containers": [{
                                           "name": "measure", "image": CRYO_IMAGE,
                                           "command": ["python", "-u", "/opt/v2/trino_measure.py"],
                                           "env": [{"name": "PHASE", "value": phase},
                                                   {"name": "TRINO", "value": f"http://{trino}.{NS}.svc:8080"},
                                                   {"name": "TARGETS", "value": json.dumps(targets)},
                                                   {"name": "QSET", "value": qset},
                                                   {"name": "WARMUP", "value": "1"}, {"name": "REPS", "value": str(reps)}]
                                                  + [{"name": k, "value": v} for k, v in (extra_env or {}).items()],
                                           "volumeMounts": [{"name": "code", "mountPath": "/opt/v2"}],
                                           "resources": {"requests": {"cpu": "200m", "memory": "256Mi"},
                                                         "limits": {"cpu": "300m", "memory": "512Mi"}}}],
                                       "volumes": [{"name": "code", "configMap": {"name": "v2-p1-code"}}]}}},
    }


def run_job(manifest, timeout=7200):
    return wait_job(*start_job(manifest), timeout=timeout)


def start_job(manifest):
    name = manifest["metadata"]["name"]
    try:
        batch.read_namespaced_job(name, NS)
        emit(event="job_exists_reuse_refused", job=name)
        raise SystemExit(f"job {name} already exists; refusing to re-run a scored step")
    except client.exceptions.ApiException as exc:
        if exc.status != 404:
            raise
    t0 = time.time()
    batch.create_namespaced_job(NS, manifest)
    emit(event="job_created", job=name)
    return name, t0


def wait_job(name, t0, timeout=7200):
    status = "timeout"
    while time.time() - t0 < timeout:
        j = batch.read_namespaced_job(name, NS)
        if j.status.succeeded:
            status = "succeeded"
            break
        if j.status.failed:
            status = "failed"
            break
        time.sleep(5)
    seconds = round(time.time() - t0, 2)
    pods = core.list_namespaced_pod(NS, label_selector=f"job-name={name}").items
    for p in pods:
        try:
            log = core.read_namespaced_pod_log(p.metadata.name, NS)
            for line in log.splitlines():
                if line.startswith(("P1 ", "P2 ", "P3 ", "TM ", "FG ", "FG_END ", "FX ", "FX_SUMMARY ", "Traceback", "SystemExit")) or "Error" in line[:80]:
                    print(f"CHILD {name} {line}", flush=True)
        except Exception as exc:
            emit(event="log_read_error", job=name, error=str(exc)[:200])
    emit(event="job_done", job=name, status=status, seconds=seconds)
    if status != "succeeded":
        raise SystemExit(f"child job {name} {status}")
    return seconds


def ensure_trino(name="bench-trino"):
    apps.patch_namespaced_deployment_scale(name, NS, {"spec": {"replicas": 1}})
    t0 = time.time()
    while time.time() - t0 < 900:
        d = apps.read_namespaced_deployment(name, NS)
        if (d.status.ready_replicas or 0) >= 1:
            emit(event="trino_ready", trino=name, seconds=round(time.time() - t0, 1))
            time.sleep(20)
            return
        time.sleep(5)
    raise SystemExit("trino not ready")


# ---------------------------------------------------------------- Cryo API
_tok = None


def call(method, path, body=None, timeout=120):
    global _tok
    if _tok is None:
        _tok = json.loads(urllib.request.urlopen(urllib.request.Request(
            API + "/auth/token", data=json.dumps({"subject": "bench-v2", "role": "admin"}).encode(),
            headers={"content-type": "application/json"}), timeout=30).read())["access_token"]
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json", "authorization": "Bearer " + _tok})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt < 4:
                _tok = None
                return call(method, path, body, timeout)
            if e.code >= 500 and attempt < 4:
                time.sleep(5)
                continue
            raise RuntimeError(f"HTTP {e.code} {method} {path}: {e.read()[:400]!r}")
        except urllib.error.URLError:
            if attempt < 4:
                time.sleep(5)
                continue
            raise


def find_by_name(path, name):
    items = call("GET", path)
    items = items.get("items", items) if isinstance(items, dict) else items
    return next((x for x in items if x.get("name") == name), None)


def cryo_ids():
    dest = find_by_name("/destinations", "v2-spark-k8s")
    preset = find_by_name("/presets", "v2-a3-default")
    if not dest or not preset:
        raise SystemExit("run the setup step first: v2-spark-k8s destination / v2-a3-default preset missing")
    return dest["id"], preset["id"]


def phase_destination(name):
    """Per-phase copy of v2-spark-k8s (same config), so concurrent phases get separate governor
    slot pools and one phase's admissions never wait on another's."""
    d = find_by_name("/destinations", name)
    if d is None:
        base = find_by_name("/destinations", "v2-spark-k8s")
        d = call("POST", "/destinations", {"name": name, "engine": base["engine"], "config": base["config"], "enabled": True})
    emit(event="phase_destination", name=name, id=d["id"], config=d.get("config"))
    return d["id"]


def cryo_scan(scope, **kw):
    t0 = time.time()
    body = {"scope": scope, "full_scan": True, "num_chunks": 1, **kw}
    r = call("POST", "/scans", body)
    run_id = r.get("run_id")
    while time.time() - t0 < 3600:
        run = call("GET", f"/runs/{run_id}")
        if run.get("status") in ("succeeded", "failed", "partial_failed", "cancelled", "partial"):
            emit(event="cryo_scan_done", scope=scope, run_id=run_id, status=run.get("status"),
                 seconds=round(time.time() - t0, 2), summary=run.get("summary"))
            if run.get("status") != "succeeded":
                raise SystemExit(f"scan {run_id} {run.get('status')}: refusing to plan on partial stats")
            return round(time.time() - t0, 2)
        time.sleep(3)
    raise SystemExit("scan timeout")


STAT_KEYS = ("total_files", "small_files_count", "total_size_gb", "total_snapshots", "total_delete_files", "manifest_count", "needs_compaction", "compaction_priority", "grade", "overall_score", "scan_at")


def tstats(st):
    s = st.get("stats") or {}
    return {k: s.get(k) for k in STAT_KEYS}


def cryo_tables(schema):
    out, offset = {}, 0
    while True:
        page = call("GET", f"/tables?limit=500&offset={offset}")
        items = page.get("items", [])
        for it in items:
            if it.get("schema_name") == schema:
                out[it["table_name"]] = it
        if len(items) < 500:
            return out
        offset += 500


def cryo_window(trial, cycle, dest_id, preset_id):
    schema = f"v2p1_r{trial}_a3"
    t0 = time.time()
    emit(event="window_start", arm="a3", trial=trial, cycle=cycle)
    scan_s = cryo_scan("schemas", schema_names=[schema])
    tables = cryo_tables(schema)
    missing = [t for t in TABLES if t not in tables]
    if missing:
        raise SystemExit(f"Cryo does not know tables {missing} in {schema}: refusing to record zero work")
    for name in TABLES:
        st = tables[name]
        emit(event="cryo_table_stats", arm="a3", trial=trial, table=name, table_id=st["id"],
             stats=tstats(st))
    t_run = time.time()
    r = call("POST", "/maintenance/run", {"table_ids": [tables[n]["id"] for n in TABLES],
                                          "destination_id": dest_id, "preset_id": preset_id,
                                          "mode": "standard", "trigger": "api"})
    run_id = r["run_id"]
    emit(event="cryo_run_submitted", run_id=run_id, job_ids=r.get("job_ids"), status=r.get("status"))
    last = None
    items = []
    while time.time() - t_run < 7200:
        jobs = call("GET", f"/runs/{run_id}/jobs")
        items = jobs.get("jobs") or jobs.get("items") or []
        states = sorted(j.get("status") for j in items)
        if states != last:
            emit(event="cryo_progress", run_id=run_id, states=states, elapsed_s=round(time.time() - t_run, 1))
            last = states
        if items and all(s in TERMINAL_JOB for s in states):
            break
        time.sleep(5)
    t_end = time.time()
    for j in items:
        try:
            d = call("GET", f"/jobs/{j['id']}/detail")
        except Exception as exc:
            d = {"detail_error": str(exc)[:300]}
        emit(event="cryo_job_detail", run_id=run_id, job=j, detail=d)
    run = call("GET", f"/runs/{run_id}")
    emit(event="window_end", arm="a3", trial=trial, cycle=cycle, run_id=run_id, run_status=run.get("status"),
         seconds=round(t_end - t0, 2), scan_seconds=scan_s, run_seconds=round(t_end - t_run, 2))
    harness_guard(items, f"trial {trial} cycle {cycle}")


def harness_guard(items, where):
    """A pod refused by the benchmark's own ResourceQuota is a harness failure, not a Cryo failure."""
    bad = [j["id"] for j in items if "exceeded quota" in (j.get("error") or "")]
    if bad:
        emit(event="harness_quota_failure", where=where, jobs=bad)
        raise SystemExit(f"benchmark quota refused Cryo pods in {where}: trial is not scorable")


# ---------------------------------------------------------------- main
def targets(trial):
    out = []
    for arm in ("a0", "a1", "a2", "a3"):
        for tb in TABLES:
            cls_name, idx = tb.rsplit("_", 1)
            base = (CLASS_INDEX[cls_name] * 10 + int(idx)) * 10**10
            out.append({"label": f"r{trial}_{arm}_{tb}", "schema": f"v2p1_r{trial}_{arm}", "table": tb,
                        "point_id": base + 7, "filter_date": "2026-01-15"})
    return out


def trial_run(trial):
    dest_id, preset_id = cryo_ids()
    emit(event="trial_start", trial=trial, cycles=CYCLES, dest_id=dest_id, preset_id=preset_id)
    run_job(spark_job(f"v2p1-r{trial}-gen", {"MODE": "gen", "TRIAL": trial}, 4, 12, 4))
    ensure_trino()
    run_job(measure_job(f"v2p1-r{trial}-c0-measure", f"r{trial}c0", targets(trial)))
    for cycle in range(1, CYCLES + 1):
        run_job(spark_job(f"v2p1-r{trial}-c{cycle}-ingest", {"MODE": "ingest", "TRIAL": trial, "CYCLE": cycle}, 4, 12, 4))
        order = ROTATIONS[(trial + cycle) % 3]
        emit(event="cycle_order", trial=trial, cycle=cycle, order=order)
        for arm in order:
            if arm == "a3":
                cryo_window(trial, cycle, dest_id, preset_id)
            else:
                emit(event="window_start", arm=arm, trial=trial, cycle=cycle)
                secs = run_job(spark_job(f"v2p1-r{trial}-c{cycle}-{arm}", {"MODE": "diy", "TRIAL": trial, "CYCLE": cycle, "DIY_ARM": arm}, 3, 6, 3))
                emit(event="window_end", arm=arm, trial=trial, cycle=cycle, seconds=secs)
        ensure_trino()
        run_job(measure_job(f"v2p1-r{trial}-c{cycle}-measure", f"r{trial}c{cycle}", targets(trial)))
    emit(event="trial_end", trial=trial)


def setup():
    """Create the V2 destination (nested spark_k8s keys, image pinned) and the A3 preset.

    The A3 preset copies Cryo's shipped default preset (default-spark-compact): same mode,
    operations and params. Only guardrails.cooldown_hours is dropped, because one benchmark
    cycle stands in for one day (time compression; documented in the protocol).
    """
    src = find_by_name("/destinations", "bench-spark-k8s")
    cfg = {k: src["config"][k] for k in ("catalog", "namespace", "source_id", "catalog_uri",
                                         "submit_mode", "catalog_impl", "catalog_warehouse")}
    cfg["slot_cap"] = 3
    cfg["spark_k8s"] = {"image": SPARK_IMAGE, "service_account": "cryo-bench-spark", "namespace": NS,
                        "catalog_uri": cfg["catalog_uri"], "catalog_warehouse": cfg["catalog_warehouse"],
                        "catalog_impl": "rest", "executor_instances": 16, "executor_cpu_milli": 4000,
                        "executor_mem_mib": 8192, "driver_cpu_milli": 1000, "driver_mem_mib": 2048}
    dest = find_by_name("/destinations", "v2-spark-k8s")
    if dest is None:
        dest = call("POST", "/destinations", {"name": "v2-spark-k8s", "engine": "spark_emr", "config": cfg, "enabled": True})
    emit(event="setup_destination", id=dest["id"], config=dest.get("config"))
    default = find_by_name("/presets", "default-spark-compact")
    emit(event="shipped_default_preset", preset=default)
    preset = find_by_name("/presets", "v2-a3-default")
    if preset is None:
        guard = {k: v for k, v in (default.get("guardrails") or {}).items() if k != "cooldown_hours"}
        preset = call("POST", "/presets", {"name": "v2-a3-default", "destination_id": dest["id"],
                                           "mode": default["mode"], "operations": default["operations"],
                                           "params": default["params"], "guardrails": guard,
                                           "description": "CryoBench V2 A3: copy of default-spark-compact, cooldown removed for time compression",
                                           "enabled": True})
    emit(event="setup_preset", preset=preset)


def recover():
    """Re-create the V1 source and base destination after the Cryo database was lost
    (2026-10-22 ~07:00 UTC, Postgres had no volume), with the configs recorded in V1, then setup()."""
    wh = "s3://<benchmark-bucket>/warehouse/"
    uri = f"http://iceberg-rest.{NS}.svc:8181"
    src = find_by_name("/sources", "bench-rest")
    if src is None:
        src = call("POST", "/sources", {"name": "bench-rest", "type": "rest", "config": {
            "uri": uri, "warehouse": wh, "s3.region": "<region>",
            "s3.access-key-id": os.environ["AWS_ACCESS_KEY_ID"],
            "s3.secret-access-key": os.environ["AWS_SECRET_ACCESS_KEY"]}})
    emit(event="recover_source", id=src["id"])
    dest = find_by_name("/destinations", "bench-spark-k8s")
    if dest is None:
        dest = call("POST", "/destinations", {"name": "bench-spark-k8s", "engine": "spark_emr", "config": {
            "submit_mode": "k8s", "namespace": NS, "service_account": "cryo-bench-spark", "catalog": "bench",
            "catalog_impl": "rest", "catalog_uri": uri, "catalog_warehouse": wh, "image": SPARK_IMAGE,
            "source_id": src["id"]}})
    emit(event="recover_destination", id=dest["id"])
    setup()


if __name__ == "__main__":
    if os.environ.get("ACTION") == "recover":
        recover()
    elif os.environ.get("ACTION") == "setup":
        setup()
    else:
        for tr in TRIALS:
            trial_run(tr)
        emit(event="controller_done", trials=TRIALS)
