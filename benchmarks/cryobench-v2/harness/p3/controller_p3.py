"""CryoBench V2 phase 3 controller: continuous CDC with concurrent maintenance.

Arms (same micro-batches, applied by one stream job):
  a0  no maintenance
  a1  naive scheduled Spark DAG every A1_EVERY seconds (Airflow-style fixed schedule)
  a3  Cryo every A3_EVERY seconds: schema scan, then POST /maintenance/run with no operations
      field, so Cryo's planner decides every operation (same as its cron path)
Trino probes on all arms every PROBE_EVERY seconds. Maintenance and probes overlap the stream
on purpose: commit conflicts between writers and maintenance are part of what is measured.
"""
import json, os, threading, time
import controller as c1

TRIAL = int(os.environ.get("TRIALS", "1").split(",")[0])
BATCHES = int(os.environ.get("BATCHES", "36"))
PERIOD = int(os.environ.get("PERIOD", "200"))
A1_EVERY = int(os.environ.get("A1_EVERY", "1800"))
A3_EVERY = int(os.environ.get("A3_EVERY", "600"))
PROBE_EVERY = int(os.environ.get("PROBE_EVERY", "600"))
SCHEMA = lambda arm: f"v2p3_r{TRIAL}_{arm}"
QUERIES = {
    "c_agg": "SELECT status, count(*), sum(amount) FROM {t} GROUP BY status ORDER BY status",
    "c_point": "SELECT id, status, amount FROM {t} WHERE id = {pid}",
}
stop = threading.Event()
TRINO = os.environ.get("TRINO_NAME", "bench-trino-c")


def targets(tag):
    return [{"label": f"r{TRIAL}_{arm}_orders_{tag}", "schema": SCHEMA(arm), "table": "orders",
             "point_id": 123457, "filter_date": "2026-01-15"} for arm in ("a0", "a1", "a3")]


def sleep_until(t):
    while not stop.is_set() and time.time() < t:
        time.sleep(5)
    return not stop.is_set()


def cryo_once(n, dest_id, preset_id):
    t0 = time.time()
    c1.emit(event="p3_window_start", arm="a3", n=n)
    c1.cryo_scan("schemas", schema_names=[SCHEMA("a3")])
    tables = c1.cryo_tables(SCHEMA("a3"))
    if not {"orders", "events"} <= set(tables):
        raise SystemExit(f"Cryo does not know {SCHEMA('a3')} tables: refusing to record zero work")
    for name in ("orders", "events"):
        st = tables[name]
        c1.emit(event="p3_cryo_stats", n=n, table=name, stats=c1.tstats(st))
    t_run = time.time()
    r = c1.call("POST", "/maintenance/run", {"table_ids": [tables[x]["id"] for x in ("orders", "events")],
                                             "destination_id": dest_id, "preset_id": preset_id,
                                             "mode": "standard", "trigger": "api"})
    items = []
    while time.time() - t_run < 3600:
        items = (lambda j: j.get("jobs") or j.get("items") or [])(c1.call("GET", f"/runs/{r['run_id']}/jobs"))
        if items and all(j.get("status") in c1.TERMINAL_JOB for j in items):
            break
        time.sleep(5)
    for j in items:
        try:
            d = c1.call("GET", f"/jobs/{j['id']}/detail")
        except Exception as exc:
            d = {"detail_error": str(exc)[:300]}
        c1.emit(event="p3_cryo_job", n=n, run_id=r["run_id"], job=j, detail=d)
    c1.emit(event="p3_window_end", arm="a3", n=n, run_id=r["run_id"], seconds=round(time.time() - t0, 2),
            run_seconds=round(time.time() - t_run, 2), states=[j.get("status") for j in items])
    c1.harness_guard(items, f"p3 window {n}")


def loop(kind, every, fn, t_start):
    n = 1
    while sleep_until(t_start + n * every):
        try:
            fn(n)
        except BaseException as exc:
            c1.emit(event="p3_loop_error", loop=kind, n=n, error=f"{type(exc).__name__}: {str(exc)[:300]}")
        n += 1


def main():
    _, preset_id = c1.cryo_ids()
    dest_id = c1.phase_destination("v2-spark-k8s-p3")
    c1.emit(event="p3_start", trial=TRIAL, batches=BATCHES, period=PERIOD, a1_every=A1_EVERY, a3_every=A3_EVERY)
    c1.run_job(c1.spark_job(f"v2p3-r{TRIAL}-init", {"MODE": "init", "TRIAL": TRIAL}, 4, 12, 4, script="spark_p3.py"))
    c1.ensure_trino(TRINO)
    env = {"QUERIES_JSON": json.dumps(QUERIES)}
    c1.run_job(c1.measure_job(f"v2p3-r{TRIAL}-probe-0", f"p3r{TRIAL}p0", targets("p0"), ",".join(QUERIES), env, reps=2, trino=TRINO))
    name, t0 = c1.start_job(c1.spark_job(f"v2p3-r{TRIAL}-stream", {"MODE": "stream", "TRIAL": TRIAL, "BATCHES": BATCHES,
                                                                    "PERIOD": PERIOD}, 4, 12, 4, script="spark_p3.py",
                                         deadline=BATCHES * PERIOD + 7200))
    a1 = lambda n: c1.run_job(c1.spark_job(f"v2p3-r{TRIAL}-a1-w{n}", {"MODE": "diy", "TRIAL": TRIAL, "DIY_ARM": "a1"},
                                           3, 6, 3, script="spark_p3.py"))
    a3 = lambda n: cryo_once(n, dest_id, preset_id)
    probe = lambda n: c1.run_job(c1.measure_job(f"v2p3-r{TRIAL}-probe-{n}", f"p3r{TRIAL}p{n}", targets(f"p{n}"),
                                                ",".join(QUERIES), env, reps=2, trino=TRINO))
    threads = [threading.Thread(target=loop, args=(k, e, f, t0), daemon=True)
               for k, e, f in (("a1", A1_EVERY, a1), ("a3", A3_EVERY, a3), ("probe", PROBE_EVERY, probe))]
    for th in threads:
        th.start()
    try:
        c1.wait_job(name, t0, timeout=BATCHES * PERIOD + 7200)
    finally:
        stop.set()
        for th in threads:
            th.join(timeout=7200)
    c1.run_job(c1.measure_job(f"v2p3-r{TRIAL}-probe-final", f"p3r{TRIAL}final", targets("final"),
                              ",".join(QUERIES), env, reps=3, trino=TRINO))
    c1.emit(event="p3_done", trial=TRIAL)


if __name__ == "__main__":
    main()
