"""CryoBench V2 phase 4 controller: fleet-scale control-plane test on real registered tables.

Per fleet size N (FLEETS env, default 100,1000,10000):
  1. create N real Iceberg tables (fleet_fixtures.py), ground truth deterministic by index
  2. Cryo scan scope=schemas, num_chunks=1 (discovery), then again (steady-state rescan)
  3. compare Cryo's per-table stats with ground truth
  4. cron path: preset v2-p4-n{N} (planner defaults, params {}), rule schema_name = fleet,
     schedule "* * * * *"; the Cryo scheduler (scaled to 1) builds the run; the preset is
     disabled as soon as one run exists; jobs are followed to terminal state
  5. #154 accounting (N=1000 only): one num_chunks=4 scan, counted against N
Control-plane pod CPU/memory sampled from metrics.k8s.io every 15 s (`CP` events).
The scanner cron is disabled for the duration and restored afterwards.
"""
import collections, datetime, os, signal, threading, time
import controller as c1
from kubernetes import client

FLEETS = [int(x) for x in os.environ.get("FLEETS", "100,1000,10000").split(",")]
# Resume after a lost controller: N -> "discovery_run_id,rescan_run_id" already completed by Cryo.
# Fixture creation and both scans are skipped for these N; timings come from Cryo's run records.
RECORDED_SCANS = {int(k): v.split(",") for k, v in
                  (p.split(":", 1) for p in os.environ.get("RECORDED_SCANS", "").split(";") if p)}
NEEDLES = 5
stop = threading.Event()


def ground_truth(n):
    needle_idx = {int(i * n / NEEDLES) + n // (2 * NEEDLES) for i in range(NEEDLES)}
    out = {}
    for i in range(n):
        k, files = ("needle", 130) if i in needle_idx else (("healthy", 1) if i % 2 == 0 else ("fragmented", 12))
        out[f"t{i:05d}"] = (k, files)
    return out


def sampler():
    api = client.CustomObjectsApi()
    while not stop.is_set():
        try:
            items = api.list_namespaced_custom_object("metrics.k8s.io", "v1beta1", c1.NS, "pods")["items"]
            for it in items:
                name = it["metadata"]["name"]
                if name.startswith(("cryo-bench-", "cryo-postgres", "cryo-redis", "iceberg-rest")):
                    for ct in it["containers"]:
                        print("CP " + c1.json.dumps({"pod": name, "container": ct["name"], "cpu": ct["usage"]["cpu"],
                                                     "mem": ct["usage"]["memory"], "t": round(time.time(), 1)}), flush=True)
        except Exception as exc:
            c1.emit(event="p4_sampler_unavailable", error=str(exc)[:200])
            return
        stop.wait(15)


def fixture_job(n):
    m = c1.measure_job(f"v2p4-fx-n{n}", "fx", [], extra_env={"FLEET_N": str(n), "THREADS": "16"})
    ct = m["spec"]["template"]["spec"]["containers"][0]
    ct["command"] = ["python", "-u", "/opt/v2/fleet_fixtures.py"]
    ct["env"] = c1.s3_env() + [{"name": "FLEET_N", "value": str(n)}, {"name": "THREADS", "value": "16"}]
    ct["resources"] = {"requests": {"cpu": "2", "memory": "4Gi"}, "limits": {"cpu": "2", "memory": "4Gi"}}
    m["metadata"]["labels"]["purpose"] = "v2p4-fixtures"
    return m


def accuracy(n, schema):
    truth = ground_truth(n)
    tables = c1.cryo_tables(schema)
    missing = sorted(set(truth) - set(tables))
    extra = sorted(set(tables) - set(truth))
    files_ok = small_ok = 0
    mism = []
    for name, (k, files) in truth.items():
        st = tables.get(name)
        if st is None:
            continue
        tf, sf = (st.get("stats") or {}).get("total_files"), (st.get("stats") or {}).get("small_files_count")
        files_ok += tf == files
        small_ok += sf == files
        if (tf != files or sf != files) and len(mism) < 20:
            mism.append({"table": name, "class": k, "truth_files": files, "total_files": tf, "small_files_count": sf})
    dist = collections.defaultdict(collections.Counter)
    for name, (k, _) in truth.items():
        st = (tables.get(name) or {}).get("stats") or {}
        dist[k][f"total={st.get('total_files')},small={st.get('small_files_count')}"] += 1
    c1.emit(event="p4_accuracy", n=n, known=len(tables), missing=len(missing), missing_sample=missing[:20],
            extra=len(extra), total_files_correct=files_ok, small_files_correct=small_ok, mismatch_sample=mism,
            stats_by_class={k: dict(v) for k, v in dist.items()})
    return tables


def cron_fire(n, schema, dest_id, tables):
    name = f"v2p4-n{n}"
    p = c1.find_by_name("/presets", name)
    if p is not None:
        prior = c1.call("GET", f"/runs?preset_id={p['id']}&limit=10")
        prior = prior if isinstance(prior, list) else prior.get("items", [])
        if prior or p.get("enabled") or c1.call("GET", f"/presets/{p['id']}/rules"):
            raise SystemExit(f"preset {name} already exists with runs or rules: refusing to reuse a scored step")
        c1.emit(event="p4_reused_unused_preset", n=n, preset=p)
    else:
        p = c1.call("POST", "/presets", {"name": name, "destination_id": dest_id, "mode": "standard", "operations": [],
                                         "params": {}, "guardrails": {}, "enabled": False, "schedule_cron": "* * * * *",
                                         "description": "CryoBench V2 phase 4 fleet cron test (planner defaults)"})
    c1.call("POST", f"/presets/{p['id']}/rules", {"predicate": {"field": "schema_name", "op": "=", "value": schema}})
    t_enable = time.time()
    c1.call("PATCH", f"/presets/{p['id']}", {"enabled": True})
    c1.emit(event="p4_cron_enabled", n=n, preset_id=p["id"])
    run = None
    while time.time() - t_enable < 900:
        runs = c1.call("GET", f"/runs?preset_id={p['id']}&limit=10")
        runs = runs if isinstance(runs, list) else runs.get("items", [])
        if runs:
            run = runs[-1]
            break
        time.sleep(2)
    c1.call("PATCH", f"/presets/{p['id']}", {"enabled": False})
    t_run = time.time()
    if run is None:
        c1.emit(event="p4_cron_no_run", n=n, seconds=round(t_run - t_enable, 1))
        return
    c1.emit(event="p4_cron_run", n=n, run_id=run["id"], seconds_to_run=round(t_run - t_enable, 1), run=run)
    time.sleep(70)
    extra = c1.call("GET", f"/runs?preset_id={p['id']}&limit=10")
    extra = extra if isinstance(extra, list) else extra.get("items", [])
    c1.emit(event="p4_cron_runs_after_disable", n=n, runs=len(extra))
    truth = ground_truth(n)
    id_to_name = {st["id"]: nm for nm, st in tables.items()}
    last, jobs = None, []
    while time.time() - t_run < 3 * 3600:
        jobs = c1.call("GET", f"/runs/{run['id']}/jobs").get("jobs", [])
        counts = dict(collections.Counter(j["status"] for j in jobs))
        if counts != last:
            c1.emit(event="p4_cron_progress", n=n, counts=counts, elapsed_s=round(time.time() - t_run, 1))
            last = counts
        if jobs and all(j["status"] in c1.TERMINAL_JOB for j in jobs):
            break
        time.sleep(10)
    per_table = collections.Counter(j["table_id"] for j in jobs)
    needles = {nm for nm, (k, _) in truth.items() if k == "needle"}
    by_class = collections.defaultdict(collections.Counter)
    ops_by_class = collections.defaultdict(collections.Counter)
    for j in jobs:
        nm = id_to_name.get(j["table_id"], "?")
        k = truth.get(nm, ("unknown",))[0]
        by_class[k][j["status"]] += 1
        ops = tuple(sorted(o["type"] for o in ((j.get("plan") or {}).get("operations") or [])))
        ops_by_class[k][",".join(ops) or "(none)"] += 1
    in_run = {id_to_name.get(j["table_id"]) for j in jobs}
    c1.emit(event="p4_cron_done", n=n, run_id=run["id"], jobs=len(jobs), seconds=round(time.time() - t_run, 1),
            duplicates=sum(1 for v in per_table.values() if v > 1), needles_in_run=sorted(needles & in_run),
            needles_missed=sorted(needles - in_run), status_by_class={k: dict(v) for k, v in by_class.items()},
            ops_by_class={k: dict(v) for k, v in ops_by_class.items()},
            starts=[j.get("started_at") for j in jobs if j.get("started_at")][:5],
            errors=collections.Counter((j.get("error") or "")[:120] for j in jobs if j.get("error")).most_common(5))


def recorded_scan(n, kind, run_id):
    run = c1.call("GET", f"/runs/{run_id}")
    if run.get("status") != "succeeded":
        raise SystemExit(f"recorded scan {run_id} is {run.get('status')}")
    ts = lambda s: datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    summ = run.get("summary") or {}
    start, end = run.get("created_at") or summ["started_at"], run.get("finished_at") or summ["finished_at"]
    secs = round(ts(end) - ts(start), 1)
    c1.emit(event="p4_scan", n=n, kind=kind, seconds=secs, tables_per_min=round(n / secs * 60, 1),
            source="cryo_run_record", run_id=run_id, summary=run.get("summary"))


def _sigterm(signum, frame):
    raise SystemExit(f"signal {signum}")


def main():
    signal.signal(signal.SIGTERM, _sigterm)
    c1.cryo_ids()
    dest_id = c1.phase_destination("v2-spark-k8s-p4")
    scanner_before = c1.call("GET", "/scanner/config")
    c1.emit(event="p4_start", fleets=FLEETS, scanner_config_before=scanner_before)
    c1.call("PATCH", "/scanner/config", {"enabled": False})
    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    c1.apps.patch_namespaced_deployment_scale("cryo-bench-scheduler", c1.NS, {"spec": {"replicas": 1}})
    try:
        for n in FLEETS:
            schema = f"v2p4_n{n}"
            if n in RECORDED_SCANS:
                recorded_scan(n, "discovery", RECORDED_SCANS[n][0])
                recorded_scan(n, "rescan", RECORDED_SCANS[n][1])
            else:
                c1.run_job(fixture_job(n), timeout=4 * 3600)
                s1 = c1.cryo_scan("schemas", schema_names=[schema])
                c1.emit(event="p4_scan", n=n, kind="discovery", seconds=s1, tables_per_min=round(n / s1 * 60, 1))
                s2 = c1.cryo_scan("schemas", schema_names=[schema])
                c1.emit(event="p4_scan", n=n, kind="rescan", seconds=s2, tables_per_min=round(n / s2 * 60, 1))
            tables = accuracy(n, schema)
            if n == 1000:
                t0 = time.time()
                r = c1.call("POST", "/scans", {"scope": "schemas", "schema_names": [schema], "full_scan": True, "num_chunks": 4})
                while time.time() - t0 < 4 * 3600:
                    run = c1.call("GET", f"/runs/{r['run_id']}")
                    if run.get("status") in ("succeeded", "failed", "partial_failed", "cancelled", "partial"):
                        break
                    time.sleep(5)
                c1.emit(event="p4_chunked_scan", n=n, num_chunks=4, status=run.get("status"),
                        seconds=round(time.time() - t0, 1), summary=run.get("summary"))
            cron_fire(n, schema, dest_id, tables)
    finally:
        c1.apps.patch_namespaced_deployment_scale("cryo-bench-scheduler", c1.NS, {"spec": {"replicas": 0}})
        c1.call("PATCH", "/scanner/config", {"enabled": scanner_before["enabled"]})
        stop.set()
        c1.emit(event="p4_done")


if __name__ == "__main__":
    main()
