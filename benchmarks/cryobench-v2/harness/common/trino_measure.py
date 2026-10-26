"""Read-only Trino measurement: physical health, integrity fingerprint and timed queries.

Trino runs single-tenant with the Iceberg metadata cache disabled and queries run
serially, so the delta of the S3FileSystemStats JMX `total` counter across one query is
that query's S3 request count (data plus Iceberg metadata). Client-side counts, not billing.

TARGETS: JSON list of {"label", "schema", "table", "point_id", "filter_date"}.
Emits one `TM {...}` JSON line per measurement.
"""
import hashlib, json, os, random, time, urllib.request

TRINO = os.environ.get("TRINO", "http://bench-trino.cryo-bench.svc:8080")
TARGETS = json.loads(os.environ["TARGETS"])
PHASE = os.environ["PHASE"]
WARMUP = int(os.environ.get("WARMUP", "1"))
REPS = int(os.environ.get("REPS", "3"))
QSET = os.environ.get("QSET", "q_agg,q_filter,q_point").split(",")
DO_HEALTH = os.environ.get("HEALTH", "1") == "1"
SMALL = 32 * 1024 * 1024
JMX = 'SELECT * FROM jmx.current."io.trino.filesystem.s3:name=iceberg,type=s3filesystemstats"'

QUERIES = {
    "q_agg": "SELECT category, count(*), sum(amount) FROM {t} GROUP BY category ORDER BY category",
    "q_filter": "SELECT count(*), sum(qty) FROM {t} WHERE event_date = DATE '{d}' AND customer_id < 5000",
    "q_point": "SELECT id, amount, status FROM {t} WHERE id = {pid}",
    "q_full": "SELECT count(*), sum(length(note)) FROM {t}",
    "q_join": ("SELECT a.category, count(*) FROM {t} a JOIN (SELECT customer_id, max(amount) m FROM {t} "
               "WHERE qty = 3 GROUP BY customer_id) b ON a.customer_id = b.customer_id "
               "WHERE a.amount > b.m / 2 GROUP BY a.category ORDER BY a.category"),
}


if os.environ.get("QUERIES_JSON"):
    QUERIES.update(json.loads(os.environ["QUERIES_JSON"]))


def run(sql, timeout=900):
    hdr = {"X-Trino-User": "bench", "X-Trino-Source": "cryobench-v2"}
    t0 = time.perf_counter()
    r = json.load(urllib.request.urlopen(urllib.request.Request(f"{TRINO}/v1/statement", data=sql.encode(), headers=hdr), timeout=60))
    rows, cols = [], None
    while True:
        rows += r.get("data", [])
        cols = cols or r.get("columns")
        if "error" in r:
            raise RuntimeError(r["error"].get("message", "")[:400])
        if "nextUri" not in r:
            break
        r = json.load(urllib.request.urlopen(urllib.request.Request(r["nextUri"], headers=hdr), timeout=timeout))
    return rows, cols, time.perf_counter() - t0, r.get("stats", {}), r.get("id")


def s3_total():
    rows, cols, *_ = run(JMX)
    for c, v in zip(cols, rows[0]):
        if c["name"] == "total.calls.totalcount":
            return int(v)
    raise RuntimeError("S3 total counter not found")


ID = os.environ.get("FP_ID_COL", "id")


def emit(**kw):
    kw.update(phase=PHASE, utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    print("TM " + json.dumps(kw, default=str), flush=True)


def fqn(tg):
    return f'iceberg.{tg["schema"]}.{tg["table"]}', f'iceberg.{tg["schema"]}."{tg["table"]}'


if DO_HEALTH:
    for tg in TARGETS:
        t, meta = fqn(tg)
        rec = {"kind": "health", "label": tg["label"], "schema": tg["schema"], "table": tg["table"]}
        try:
            f = run(f'SELECT count_if(content = 0), count_if(content = 0 AND file_size_in_bytes < {SMALL}), '
                    f'count_if(content <> 0), sum(IF(content = 0, file_size_in_bytes, 0)), '
                    f'sum(IF(content <> 0, file_size_in_bytes, 0)), sum(IF(content = 0, record_count, 0)) FROM {meta}$files"')[0][0]
            snaps = run(f'SELECT count(*) FROM {meta}$snapshots"')[0][0][0]
            mans = run(f'SELECT count(*) FROM {meta}$manifests"')[0][0][0]
            rec.update(ok=True, data_files=f[0], small_files=f[1], delete_files=f[2], data_bytes=f[3],
                       delete_bytes=f[4], data_records=f[5], snapshots=snaps, manifests=mans)
        except Exception as exc:
            rec.update(ok=False, error=f"{type(exc).__name__}: {str(exc)[:300]}")
        try:
            fp = run(f"SELECT count(*), checksum({ID}), CAST(sum(amount) AS varchar), "
                     f"checksum(concat(CAST({ID} AS varchar), status, CAST(amount AS varchar))) FROM {t}")[0][0]
            rec.update(fp_ok=True, rows=fp[0], fp_id=fp[1], sum_amount=fp[2], fp_row=fp[3])
        except Exception as exc:
            rec.update(fp_ok=False, fp_error=f"{type(exc).__name__}: {str(exc)[:300]}")
        emit(**rec)

rng = random.Random(int(os.environ.get("ORDER_SEED", "20261022")))
for rnd in range(WARMUP + REPS):
    work = [(tg, q) for tg in TARGETS for q in QSET]
    rng.shuffle(work)
    for tg, q in work:
        t, _ = fqn(tg)
        sql = QUERIES[q].format(t=t, d=tg.get("filter_date", "2026-01-15"), pid=tg.get("point_id", 0),
                                dim=tg.get("dim", ""))
        rec = {"kind": "query", "label": tg["label"], "schema": tg["schema"], "table": tg["table"],
               "query": q, "round": rnd, "scored": rnd >= WARMUP}
        try:
            before = s3_total()
            rows, cols, wall, stats, qid = run(sql)
            after = s3_total()
            rec.update(ok=True, wall_s=round(wall, 4), query_id=qid, elapsed_ms=stats.get("elapsedTimeMillis"),
                       cpu_ms=stats.get("cpuTimeMillis"), processed_rows=stats.get("processedRows"),
                       physical_input_bytes=stats.get("physicalInputBytes"), splits=stats.get("totalSplits"),
                       s3_requests=after - before,
                       result_sha256=hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()[:16])
        except Exception as exc:
            rec.update(ok=False, error=f"{type(exc).__name__}: {str(exc)[:300]}")
        emit(**rec)
