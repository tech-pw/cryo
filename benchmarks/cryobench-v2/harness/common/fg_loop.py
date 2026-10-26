"""Foreground Trino workload for the workload-protection phase.

Runs QUERIES in a fixed rotation against TARGET (an untouched table) until the stop condition:
  DURATION seconds elapsed (baseline window), or
  STOP_CRYO_RUN: every job of that Cryo run is terminal, or
  STOP_K8S_JOB: that Kubernetes Job has succeeded or failed.
Emits `FG {...}` per query with latency, S3 requests and status.
"""
import json, os, time, urllib.request

TRINO = os.environ["TRINO"]
TARGET = os.environ["TARGET"]
LABEL = os.environ["LABEL"]
DURATION = float(os.environ.get("DURATION", "0"))
STOP_CRYO_RUN = os.environ.get("STOP_CRYO_RUN", "")
STOP_K8S_JOB = os.environ.get("STOP_K8S_JOB", "")
MAX_SECONDS = float(os.environ.get("MAX_SECONDS", "14400"))
API = "http://cryo-bench-api.cryo-bench.svc:8000/api/v1"
NS = "cryo-bench"
QUERIES = [
    ("fg_day", f"SELECT region, count(*), sum(amount) FROM {TARGET} WHERE event_date = DATE '2026-02-20' GROUP BY region"),
    ("fg_week", f"SELECT category, sum(amount) FROM {TARGET} WHERE event_date BETWEEN DATE '2026-01-21' AND DATE '2026-01-27' GROUP BY category"),
    ("fg_point", f"SELECT order_id, status FROM {TARGET} WHERE order_id = 400000000011"),
]
JMX = 'SELECT * FROM jmx.current."io.trino.filesystem.s3:name=iceberg,type=s3filesystemstats"'
TERMINAL = {"succeeded", "failed", "skipped", "partial", "cancelled", "deferred"}


def run(sql):
    hdr = {"X-Trino-User": "bench", "X-Trino-Source": "cryobench-v2-fg"}
    t0 = time.perf_counter()
    r = json.load(urllib.request.urlopen(urllib.request.Request(f"{TRINO}/v1/statement", data=sql.encode(), headers=hdr), timeout=60))
    rows, cols = [], None
    while True:
        rows += r.get("data", [])
        cols = cols or r.get("columns")
        if "error" in r:
            raise RuntimeError(r["error"].get("message", "")[:300])
        if "nextUri" not in r:
            break
        r = json.load(urllib.request.urlopen(urllib.request.Request(r["nextUri"], headers=hdr), timeout=900))
    return rows, cols, time.perf_counter() - t0, r.get("stats", {})


def s3_total():
    rows, cols, *_ = run(JMX)
    return next(int(v) for c, v in zip(cols, rows[0]) if c["name"] == "total.calls.totalcount")


_tok = None


def cryo_done():
    global _tok
    if _tok is None:
        _tok = json.loads(urllib.request.urlopen(urllib.request.Request(
            API + "/auth/token", data=json.dumps({"subject": "bench-v2-fg", "role": "viewer"}).encode(),
            headers={"content-type": "application/json"}), timeout=30).read())["access_token"]
    req = urllib.request.Request(f"{API}/runs/{STOP_CRYO_RUN}/jobs", headers={"authorization": "Bearer " + _tok})
    j = json.loads(urllib.request.urlopen(req, timeout=30).read())
    items = j.get("jobs") or j.get("items") or []
    return bool(items) and all(x.get("status") in TERMINAL for x in items)


def k8s_done():
    from kubernetes import client, config
    config.load_incluster_config()
    s = client.BatchV1Api().read_namespaced_job(STOP_K8S_JOB, NS).status
    return bool(s.succeeded or s.failed)


t_start = time.time()
i = 0
while True:
    el = time.time() - t_start
    if el > MAX_SECONDS:
        break
    if DURATION and el >= DURATION:
        break
    if i % 3 == 0 and STOP_CRYO_RUN and cryo_done():
        break
    if i % 3 == 0 and STOP_K8S_JOB and k8s_done():
        break
    name, sql = QUERIES[i % len(QUERIES)]
    rec = {"label": LABEL, "query": name, "i": i, "t_rel": round(el, 2), "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        b = s3_total()
        rows, cols, wall, st = run(sql)
        rec.update(ok=True, wall_s=round(wall, 4), elapsed_ms=st.get("elapsedTimeMillis"), cpu_ms=st.get("cpuTimeMillis"),
                   queued_ms=st.get("queuedTimeMillis"), s3_requests=s3_total() - b)
    except Exception as exc:
        rec.update(ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
    print("FG " + json.dumps(rec), flush=True)
    i += 1
print("FG_END " + json.dumps({"label": LABEL, "queries": i, "seconds": round(time.time() - t_start, 1)}), flush=True)
