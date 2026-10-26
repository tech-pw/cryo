"""CryoBench V2 phase 3: continuous CDC ingestion with concurrent maintenance.

MODE=init    create bench.v2p3_r{TRIAL}_{arm}.orders (merge-on-read upsert target) and .events
             (append-only micro-batches) for every arm, identical initial content.
MODE=stream  every PERIOD seconds apply one identical micro-batch to every arm:
             MERGE of UPDATES updated + INSERTS new order rows into orders, and EVENT_FILES small
             files appended to events. Commit conflicts with concurrent maintenance are retried
             (up to 6 attempts) and every attempt is recorded. Health of every table is emitted
             every HEALTH_EVERY batches and at the end.
MODE=diy     naive scheduled-Spark DAG (same as phase 1 A1) on one arm's tables.
"""
import json, os, time
from datetime import datetime, timedelta, timezone
from pyspark.sql import SparkSession

WAREHOUSE = "s3://<benchmark-bucket>/warehouse/"
assert WAREHOUSE.startswith("s3://<benchmark-bucket>/")
MODE = os.environ["MODE"]
TRIAL = int(os.environ["TRIAL"])
SEED = 11000 + TRIAL
ARMS = os.environ.get("ARMS", "a0,a1,a3").split(",")
BATCHES = int(os.environ.get("BATCHES", "36"))
PERIOD = float(os.environ.get("PERIOD", "200"))
HEALTH_EVERY = int(os.environ.get("HEALTH_EVERY", "3"))
INIT_ROWS = 600_000
UPDATES, INSERTS, EVENT_FILES, EVENT_ROWS = 3000, 1500, 3, 2000
SMALL_FILE_BYTES = 32 * 1024 * 1024
HT_MAX_SMALL, HT_MAX_DELETE_FILES = 16, 8
TABLES = ("orders", "events")

spark = (
    SparkSession.builder.appName(f"v2p3-{MODE}-r{TRIAL}")
    .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
    .config("spark.sql.catalog.bench", "org.apache.iceberg.spark.SparkCatalog")
    .config("spark.sql.catalog.bench.type", "rest")
    .config("spark.sql.catalog.bench.uri", "http://iceberg-rest.cryo-bench.svc:8181")
    .config("spark.sql.catalog.bench.warehouse", WAREHOUSE)
    .config("spark.sql.catalog.bench.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
    .config("spark.sql.catalog.bench.s3.access-key-id", os.environ["AWS_ACCESS_KEY_ID"])
    .config("spark.sql.catalog.bench.s3.secret-access-key", os.environ["AWS_SECRET_ACCESS_KEY"])
    .config("spark.sql.catalog.bench.client.region", "ap-south-1")
    .config("spark.sql.catalog.bench.s3.retry.num-retries", "32")
    .config("spark.sql.adaptive.enabled", "false")
    .getOrCreate()
)


def emit(kind, **kw):
    kw.update(kind=kind, trial=TRIAL, mode=MODE, utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              t=round(time.time(), 3))
    print("P3 " + json.dumps(kw, default=str), flush=True)


def fq(arm, table):
    return f"bench.v2p3_r{TRIAL}_{arm}.{table}"


def health(arm, table):
    t = fq(arm, table)
    r = spark.sql(f"""SELECT sum(IF(content=0,1,0)), sum(IF(content=0 AND file_size_in_bytes<{SMALL_FILE_BYTES},1,0)),
        sum(IF(content<>0,1,0)), sum(IF(content=0,file_size_in_bytes,0)), sum(IF(content<>0,file_size_in_bytes,0)),
        sum(IF(content=0,record_count,0)), sum(IF(content<>0,record_count,0)) FROM {t}.files""").collect()[0]
    snaps = spark.sql(f"SELECT count(*) FROM {t}.snapshots").collect()[0][0]
    manifests = spark.sql(f"SELECT count(*) FROM {t}.manifests").collect()[0][0]
    small, deletes = int(r[1] or 0), int(r[2] or 0)
    return {"data_files": int(r[0] or 0), "small_files": small, "delete_files": deletes, "data_bytes": int(r[3] or 0),
            "delete_bytes": int(r[4] or 0), "data_records": int(r[5] or 0), "delete_records": int(r[6] or 0),
            "snapshots": snaps, "manifests": manifests,
            "healthy": small <= HT_MAX_SMALL and deletes <= HT_MAX_DELETE_FILES}


def order_rows(id_expr, tag, n):
    h = lambda salt: f"xxhash64('{SEED}', '{tag}', '{salt}', r.id)"
    return f"""SELECT CAST({id_expr} AS BIGINT) AS id, pmod({h('c')}, 200000) AS customer_id,
        element_at(array('new','paid','shipped','delivered','cancelled'), CAST(pmod({h('s')}, 5) AS INT) + 1) AS status,
        CAST(pmod({h('a')}, 10000000) / 100.0 AS DECIMAL(12,2)) AS amount,
        CAST(timestamp_seconds(1767225600 + pmod({h('t')}, 8640000)) AS TIMESTAMP) AS updated_at,
        concat_ws('', transform(sequence(0, 5), i -> base64(unhex(sha2(concat('{SEED}{tag}', r.id, i), 256))))) AS payload
        FROM range(0, {n}) r"""


def event_rows(b):
    h = lambda salt: f"xxhash64('{SEED}', 'ev{b}', '{salt}', r.id)"
    return f"""SELECT CAST({b} * 1000000 + r.id AS BIGINT) AS event_id, CAST(pmod({h('o')}, {INIT_ROWS}) AS BIGINT) AS order_id,
        element_at(array('view','click','cart','pay','ship'), CAST(pmod({h('k')}, 5) AS INT) + 1) AS kind,
        CAST(timestamp_seconds(1767225600 + {b} * 200 + pmod(r.id, 200)) AS TIMESTAMP) AS ts,
        concat_ws('', transform(sequence(0, 3), i -> base64(unhex(sha2(concat('{SEED}ev{b}', r.id, i), 256))))) AS payload
        FROM range(0, {EVENT_FILES * EVENT_ROWS}, 1, {EVENT_FILES}) r"""


def init():
    for arm in ARMS:
        ns = f"bench.v2p3_r{TRIAL}_{arm}"
        spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {ns}")
        for tb in TABLES:
            if spark.sql(f"SHOW TABLES IN {ns} LIKE '{tb}'").count() > 0:
                raise SystemExit(f"refusing to overwrite {ns}.{tb}")
        mor = ("'write.update.mode'='merge-on-read', 'write.delete.mode'='merge-on-read', "
               "'write.merge.mode'='merge-on-read', ")
        spark.sql(f"""CREATE TABLE {fq(arm, 'orders')} (id BIGINT, customer_id BIGINT, status STRING, amount DECIMAL(12,2),
            updated_at TIMESTAMP, payload STRING) USING iceberg TBLPROPERTIES ({mor}'format-version'='2',
            'write.distribution-mode'='none', 'write.parquet.compression-codec'='zstd')""")
        spark.sql(f"""CREATE TABLE {fq(arm, 'events')} (event_id BIGINT, order_id BIGINT, kind STRING, ts TIMESTAMP,
            payload STRING) USING iceberg TBLPROPERTIES ('format-version'='2', 'write.distribution-mode'='none',
            'write.parquet.compression-codec'='zstd')""")
    df = spark.sql(order_rows("r.id", "init", INIT_ROWS)).repartition(4).cache()
    df.count()
    for arm in ARMS:
        df.writeTo(fq(arm, "orders")).append()
    for arm in ARMS:
        for tb in TABLES:
            emit("health", batch=-1, arm=arm, table=tb, **health(arm, tb))


def commit_with_retry(arm, table, b, fn):
    for attempt in range(1, 7):
        t0 = time.time()
        try:
            fn()
            emit("commit", arm=arm, table=table, batch=b, attempt=attempt, ok=True, seconds=round(time.time() - t0, 2))
            return True
        except Exception as exc:
            emit("commit", arm=arm, table=table, batch=b, attempt=attempt, ok=False,
                 seconds=round(time.time() - t0, 2), error=f"{type(exc).__name__}: {str(exc)[:300]}")
            time.sleep(5 * attempt)
    return False


def stream():
    t_start = time.time()
    for b in range(BATCHES):
        t_b = t_start + b * PERIOD
        if time.time() < t_b:
            time.sleep(t_b - time.time())
        upd = spark.sql(order_rows(f"pmod(xxhash64('{SEED}', 'u{b}', r.id), {INIT_ROWS + b * INSERTS})", f"u{b}", UPDATES))
        ins = spark.sql(order_rows(f"{INIT_ROWS + b * INSERTS} + r.id", f"i{b}", INSERTS))
        src = upd.dropDuplicates(["id"]).unionByName(ins).coalesce(1).cache()
        src.count()
        src.createOrReplaceTempView("cdc_src")
        ev = spark.sql(event_rows(b)).cache()
        ev.count()
        lag = round(time.time() - t_b, 2)
        for arm in ARMS:
            commit_with_retry(arm, "orders", b, lambda: spark.sql(
                f"""MERGE INTO {fq(arm, 'orders')} t USING cdc_src s ON t.id = s.id
                    WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *"""))
            commit_with_retry(arm, "events", b, lambda: ev.writeTo(fq(arm, "events")).append())
        emit("batch_done", batch=b, start_lag_s=lag, seconds=round(time.time() - t_b, 2))
        src.unpersist()
        ev.unpersist()
        if (b + 1) % HEALTH_EVERY == 0 or b == BATCHES - 1:
            for arm in ARMS:
                for tb in TABLES:
                    emit("health", batch=b, arm=arm, table=tb, **health(arm, tb))
    for arm in ARMS:
        fp = spark.sql(f"SELECT count(*), CAST(sum(CAST(xxhash64(id, status, amount) AS DECIMAL(38,0))) AS STRING) "
                       f"FROM {fq(arm, 'orders')}").collect()[0]
        emit("fingerprint", arm=arm, table="orders", rows=fp[0], fp=fp[1])


def diy():
    arm = os.environ["DIY_ARM"]
    w0 = time.time()
    emit("window_start", arm=arm)
    for tb in TABLES:
        t = fq(arm, tb)
        emit("decision_input", arm=arm, table=tb, **health(arm, tb))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        for name, sql in (("rewrite_data_files", f"CALL bench.system.rewrite_data_files(table => '{t}')"),
                          ("rewrite_manifests", f"CALL bench.system.rewrite_manifests('{t}')"),
                          ("expire_snapshots", f"CALL bench.system.expire_snapshots(table => '{t}', older_than => TIMESTAMP '{cutoff}', retain_last => 1)")):
            t0 = time.time()
            try:
                rows = [r.asDict() for r in spark.sql(sql).collect()]
                emit("op", arm=arm, table=tb, op=name, ok=True, seconds=round(time.time() - t0, 2), result=rows)
            except Exception as exc:
                emit("op", arm=arm, table=tb, op=name, ok=False, seconds=round(time.time() - t0, 2),
                     error=f"{type(exc).__name__}: {str(exc)[:300]}")
        emit("post_health", arm=arm, table=tb, **health(arm, tb))
    emit("window_end", arm=arm, seconds=round(time.time() - w0, 2))


{"init": init, "stream": stream, "diy": diy}[MODE]()
spark.stop()
