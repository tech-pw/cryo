"""CryoBench V2 phase 2 controller: ~100 GiB partitioned table, Cryo default vs tuned target sizes.

Per trial: generate base -> register four zero-copy clones (a0, c256, c128, c512) from the
same metadata file -> Cryo scan -> Trino baseline on a0 (all clones are identical by
construction) -> one Cryo optimize run per compacted arm, one at a time, in rotated order ->
Trino measurement of all four arms -> health and integrity of all four arms.

c256 uses the A3 preset (copy of Cryo's shipped default, 256 MB target from Cryo's optimize
SQL). c128 and c512 are tuned presets that differ only in target_file_size_bytes.
"""
import json, os, time
import controller as c1

TRIALS = [int(x) for x in os.environ.get("TRIALS", "1").split(",")]
TARGET_GIB = os.environ.get("TARGET_GIB", "100")
TRINO = os.environ.get("TRINO_NAME", "bench-trino-b")
ARM_PRESETS = {"c256": ("v2-a3-default", None), "c128": ("v2-p2-t128", 134217728), "c512": ("v2-p2-t512", 536870912)}
ARMS = ["a0", "c256", "c128", "c512", "d256"]
FG_BASELINE_S = int(os.environ.get("FG_BASELINE_S", "900"))
# Trials after the first register fresh clones of the first trial's base table: the maintenance
# process is replicated, data generation is not (documented in the protocol).
BASE_TRIAL = int(os.environ["BASE_TRIAL"]) if os.environ.get("BASE_TRIAL") else None
GEN_CORES = int(os.environ.get("GEN_CORES", "12"))
ROTATIONS = [["c256", "c128", "c512"], ["c128", "c512", "c256"], ["c512", "c256", "c128"]]
QUERIES = {
    "p_agg_all": "SELECT category, count(*), sum(amount) FROM {t} GROUP BY category ORDER BY category",
    "p_day_small": "SELECT region, count(*), sum(amount) FROM {t} WHERE event_date = DATE '2026-01-11' GROUP BY region ORDER BY region",
    "p_day_mid": "SELECT region, count(*), sum(amount) FROM {t} WHERE event_date = DATE '2026-01-31' GROUP BY region ORDER BY region",
    "p_day_large": "SELECT region, count(*), sum(amount) FROM {t} WHERE event_date = DATE '2026-02-20' GROUP BY region ORDER BY region",
    "p_point": "SELECT order_id, status, amount FROM {t} WHERE order_id = 100000000005",
    "p_selective": "SELECT count(*), sum(amount) FROM {t} WHERE customer_id = 1234567",
    "p_join": ("SELECT d.brand, count(*), sum(f.amount) FROM {t} f JOIN {dim} d ON f.product_id = d.product_id "
               "WHERE f.event_date BETWEEN DATE '2026-01-21' AND DATE '2026-01-27' GROUP BY d.brand ORDER BY d.brand"),
    "p_full": "SELECT count(*), sum(length(comment)) FROM {t}",
}
QSET = ",".join(QUERIES)


def ensure_presets(dest_id):
    base = c1.find_by_name("/presets", "v2-a3-default")
    for arm, (name, size) in ARM_PRESETS.items():
        if size is None:
            continue
        p = c1.find_by_name("/presets", name)
        if p is None:
            params = dict(base["params"], target_file_size_bytes=size)
            p = c1.call("POST", "/presets", {"name": name, "destination_id": dest_id, "mode": base["mode"],
                                             "operations": base["operations"], "params": params,
                                             "guardrails": base.get("guardrails") or {}, "enabled": True,
                                             "description": f"CryoBench V2 phase 2 tuned: target_file_size_bytes={size}"})
        c1.emit(event="p2_preset", arm=arm, preset=p)


def fg_job(name, trial, label, **stop):
    """Foreground Trino workload on the untouched a0 clone (workload-protection phase)."""
    env = {"TRINO": f"http://{TRINO}.{c1.NS}.svc:8080", "TARGET": f"iceberg.v2p2_r{trial}.a0", "LABEL": label}
    env.update({k: str(v) for k, v in stop.items()})
    return {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": name, "namespace": c1.NS,
                     "labels": {"benchmark-run": "20261020-spark-001", "benchmark-version": "v2", "purpose": "v2p6-fg"}},
        "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 6 * 3600, "ttlSecondsAfterFinished": 604800,
                 "template": {"metadata": {"labels": {"benchmark-version": "v2", "v2-job": name}},
                              "spec": {"restartPolicy": "Never", "serviceAccountName": "bench-v2-controller",
                                       "containers": [{
                                           "name": "fg", "image": c1.CRYO_IMAGE,
                                           "command": ["python", "-u", "/opt/v2/fg_loop.py"],
                                           "env": [{"name": k, "value": v} for k, v in env.items()],
                                           "volumeMounts": [{"name": "code", "mountPath": "/opt/v2"}],
                                           "resources": {"requests": {"cpu": "200m", "memory": "256Mi"},
                                                         "limits": {"cpu": "300m", "memory": "512Mi"}}}],
                                       "volumes": [{"name": "code", "configMap": {"name": "v2-p1-code"}}]}}},
    }


def cryo_sql_for(job):
    """Statements Cryo's Spark renderer produces for a dispatched job's plan."""
    from cryo.engines.spark.sql import render
    from cryo.planner.serialization import plan_from_dict
    return [s.sql for s in render(plan_from_dict(job["plan"]))]


def diy_compact(trial, c256_job_ids):
    """Manual Spark arm d256: Cryo's exact c256 SQL on the d256 clone, local mode, CPU matched to
    the c256 Spark pods' allocation (from pod accounting), no Cryo control plane involved."""
    import base64
    jobs = [c1.call("GET", f"/jobs/{j}") for j in c256_job_ids]
    sql = ";\n".join(s for j in jobs if j.get("plan") for s in cryo_sql_for(j))
    c1.emit(event="p2_diy_sql", trial=trial, sql=sql, source_jobs=c256_job_ids)
    cores = int(os.environ.get("DIY_CORES", "9"))
    mem = int(os.environ.get("DIY_MEM_GI", "24"))
    m = c1.spark_job(f"v2p2-r{trial}-d256", {"MODE": "diy_sql", "TRIAL": trial, "DIY_TABLE": f"bench.v2p2_r{trial}.d256",
                                             "SQL_B64": base64.b64encode(sql.encode()).decode()},
                     cores, mem, cores, script="spark_p2.py", deadline=6 * 3600)
    name, t0 = c1.start_job(m)
    fg = c1.start_job(fg_job(f"v2p6-r{trial}-fg-d256", trial, f"r{trial}_d256", STOP_K8S_JOB=name))
    secs = c1.wait_job(name, t0, timeout=6 * 3600)
    c1.wait_job(*fg, timeout=1800)
    c1.emit(event="p2_diy_done", trial=trial, seconds=secs, cores=cores, mem_gi=mem)


def cryo_optimize(trial, arm, dest_id, table_id):
    preset = c1.find_by_name("/presets", ARM_PRESETS[arm][0])
    t0 = time.time()
    r = c1.call("POST", "/maintenance/run", {"table_ids": [table_id], "destination_id": dest_id,
                                             "preset_id": preset["id"], "mode": "standard", "trigger": "api",
                                             # Clones share data files: an explicit request is the only thing
                                             # that keeps the planner from adding expire_snapshots.
                                             "operations": ["optimize"]})
    run_id = r["run_id"]
    c1.emit(event="p2_run_submitted", trial=trial, arm=arm, run_id=run_id, preset=preset["name"], job_ids=r.get("job_ids"))
    fg = c1.start_job(fg_job(f"v2p6-r{trial}-fg-{arm}", trial, f"r{trial}_{arm}", STOP_CRYO_RUN=run_id))
    last, items = None, []
    while time.time() - t0 < 4 * 3600:
        items = (lambda j: j.get("jobs") or j.get("items") or [])(c1.call("GET", f"/runs/{run_id}/jobs"))
        states = sorted(j.get("status") for j in items)
        if states != last:
            c1.emit(event="p2_progress", trial=trial, arm=arm, states=states, elapsed_s=round(time.time() - t0, 1))
            last = states
        if items and all(s in c1.TERMINAL_JOB for s in states):
            break
        time.sleep(10)
    end = time.time()
    for j in items:
        try:
            d = c1.call("GET", f"/jobs/{j['id']}/detail")
        except Exception as exc:
            d = {"detail_error": str(exc)[:300]}
        c1.emit(event="p2_job_detail", trial=trial, arm=arm, run_id=run_id, job=j, detail=d)
    c1.emit(event="p2_run_done", trial=trial, arm=arm, run_id=run_id, seconds=round(end - t0, 2),
            states=[j.get("status") for j in items])
    c1.wait_job(*fg, timeout=1800)
    c1.harness_guard(items, f"p2 trial {trial} {arm}")
    return [j["id"] for j in items]


def targets(trial, arms):
    return [{"label": f"r{trial}_{a}", "schema": f"v2p2_r{trial}", "table": a,
             "dim": f"iceberg.v2p2_r{BASE_TRIAL if BASE_TRIAL is not None else trial}.dim_product"} for a in arms]


def trial_run(trial):
    c1.cryo_ids()
    dest_id = c1.phase_destination("v2-spark-k8s-p2")
    ensure_presets(dest_id)
    c1.emit(event="p2_trial_start", trial=trial, target_gib=TARGET_GIB)
    base = BASE_TRIAL if BASE_TRIAL is not None else trial
    if base == trial and os.environ.get("DO_GEN", "1") == "1":
        c1.run_job(c1.spark_job(f"v2p2-r{trial}-gen", {"MODE": "gen", "TRIAL": trial, "TARGET_GIB": TARGET_GIB},
                                GEN_CORES, GEN_CORES * 3, GEN_CORES, script="spark_p2.py", deadline=8 * 3600),
                   timeout=8 * 3600)
    c1.run_job(c1.spark_job(f"v2p2-r{trial}-clone", {"MODE": "clone", "TRIAL": trial, "BASE_TRIAL": base},
                            2, 4, 2, script="spark_p2.py"))
    schema = f"v2p2_r{trial}"
    c1.cryo_scan("schemas", schema_names=[schema])
    tables = c1.cryo_tables(schema)
    for a in ARMS:
        if a not in tables:
            raise SystemExit(f"Cryo did not discover {schema}.{a}")
        st = tables[a]
        c1.emit(event="p2_cryo_stats", trial=trial, arm=a, table_id=st["id"],
                stats=c1.tstats(st))
    env = {"QUERIES_JSON": json.dumps(QUERIES), "FP_ID_COL": "order_id"}
    c1.ensure_trino(TRINO)
    c1.run_job(c1.measure_job(f"v2p2-r{trial}-before", f"p2r{trial}before", targets(trial, ["a0"]), QSET, env, trino=TRINO), timeout=4 * 3600)
    c1.run_job(fg_job(f"v2p6-r{trial}-fg-baseline", trial, f"r{trial}_baseline", DURATION=FG_BASELINE_S), timeout=3 * 3600)
    job_ids = {}
    for arm in ROTATIONS[trial % 3]:
        c1.cryo_scan("schemas", schema_names=[schema])
        job_ids[arm] = cryo_optimize(trial, arm, dest_id, tables[arm]["id"])
    diy_compact(trial, job_ids["c256"])
    c1.ensure_trino(TRINO)
    c1.run_job(c1.measure_job(f"v2p2-r{trial}-after", f"p2r{trial}after", targets(trial, ARMS),
                              QSET, env, trino=TRINO), timeout=6 * 3600)
    c1.emit(event="p2_trial_end", trial=trial)


if __name__ == "__main__":
    for tr in TRIALS:
        trial_run(tr)
    c1.emit(event="p2_controller_done", trials=TRIALS)
