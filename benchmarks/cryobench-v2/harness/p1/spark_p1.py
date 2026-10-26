"""CryoBench V2 phase 1: fleet generation, identical ingestion, DIY maintenance arms.

Modes (MODE env):
  gen     create the 10-table fleet for every arm of one trial (identical content)
  ingest  apply one ingestion cycle to every arm's fleet (identical batches)
  diy     run one maintenance window for arm A1 (naive) or A2 (threshold-gated)

Every table lives under bench.v2p1_r{TRIAL}_{arm}. The warehouse prefix is fixed to the
approved run prefix. Nothing is dropped or overwritten: gen refuses existing tables.
"""
import json, os, time
from datetime import datetime, timedelta, timezone
from pyspark.sql import SparkSession

WAREHOUSE = "s3://<benchmark-bucket>/warehouse/"
assert WAREHOUSE.startswith("s3://<benchmark-bucket>/")

MODE = os.environ["MODE"]
TRIAL = int(os.environ["TRIAL"])
SEED = 7000 + TRIAL
ARMS = os.environ.get("ARMS", "a0,a1,a2,a3").split(",")
CYCLE = int(os.environ.get("CYCLE", "0"))
PAYLOAD_CHUNKS = 24
SMALL_FILE_BYTES = 32 * 1024 * 1024
HT_MAX_SMALL = 16
HT_MAX_DELETE_FILES = 8

# class -> (init_commits, init_files_per_commit, cycle_commits, cycle_files_per_commit, rows_per_file)
CLASSES = {
    "healthy":    (1, 4, 1, 1, 125_000),
    "moderate":   (5, 30, 2, 20, 1_300),
    "severe":     (10, 60, 5, 30, 1_300),
    "updates":    (1, 4, 0, 0, 40_000),
    "lowtraffic": (3, 4, 0, 0, 1_300),
}
UPDATE_COMMITS_PER_CYCLE = 5
TABLES = [f"{c}_{i}" for c in CLASSES for i in (1, 2)]
CLASS_INDEX = {c: n for n, c in enumerate(CLASSES)}

spark = (
    SparkSession.builder.appName(f"v2p1-{MODE}-r{TRIAL}-c{CYCLE}")
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
    kw.update(kind=kind, trial=TRIAL, cycle=CYCLE, mode=MODE,
              utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), t=round(time.time(), 3))
    print("P1 " + json.dumps(kw, default=str), flush=True)


def fq(arm, table):
    return f"bench.v2p1_r{TRIAL}_{arm}.{table}"


def batch_df(cls_name, table_idx, batch_no, files, rows_per_file):
    """Deterministic rows. batch_no is globally unique per (table, cycle, commit)."""
    tag = f"{SEED}-{cls_name}-{table_idx}-{batch_no}"
    base = (CLASS_INDEX[cls_name] * 10 + table_idx) * 10**10 + batch_no * 10**6
    return spark.sql(f"""
      SELECT CAST({base} + b.id * {rows_per_file} + n AS BIGINT) AS id,
             date_add(DATE'2026-01-01', CAST(({batch_no} * 7 + b.id) % 90 AS INT)) AS event_date,
             CAST(conv(substr(sha2(concat_ws('-', '{tag}', 'c', CAST(b.id AS STRING), CAST(n AS STRING)), 256), 1, 6), 16, 10) AS INT) % 50000 AS customer_id,
             element_at(array('books','games','music','video','home','garden','toys','sport','food','auto','health','beauty'),
                        CAST(conv(substr(sha2(concat_ws('-', '{tag}', 'k', CAST(b.id AS STRING), CAST(n AS STRING)), 256), 1, 4), 16, 10) AS INT) % 12 + 1) AS category,
             element_at(array('new','paid','shipped','returned'), CAST(n % 4 AS INT) + 1) AS status,
             CAST(CAST(conv(substr(sha2(concat_ws('-', '{tag}', 'a', CAST(b.id AS STRING), CAST(n AS STRING)), 256), 1, 8), 16, 10) AS BIGINT) % 1000000 / 100.0 AS DECIMAL(12,2)) AS amount,
             CAST(n % 17 + 1 AS INT) AS qty,
             CAST(timestamp_seconds(1767225600 + {batch_no} * 3600 + b.id * 60 + n % 60) AS TIMESTAMP) AS ts,
             concat_ws('', transform(sequence(0, {PAYLOAD_CHUNKS - 1}), i -> base64(unhex(sha2(concat_ws('-', '{tag}', CAST(b.id AS STRING), CAST(n AS STRING), CAST(i AS STRING)), 256))))) AS note
      FROM range(0, {files}, 1, {files}) b
      LATERAL VIEW explode(sequence(0, {rows_per_file - 1})) e AS n
    """)


def create_table(arm, table, cls_name):
    t = fq(arm, table)
    ns = f"bench.v2p1_r{TRIAL}_{arm}"
    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {ns}")
    if spark.sql(f"SHOW TABLES IN {ns} LIKE '{table}'").count() > 0:
        raise SystemExit(f"refusing to overwrite existing table {t}")
    props = ["'format-version'='2'", "'write.distribution-mode'='none'",
             "'write.parquet.compression-codec'='zstd'"]
    if cls_name == "updates":
        props += ["'write.update.mode'='merge-on-read'", "'write.delete.mode'='merge-on-read'",
                  "'write.merge.mode'='merge-on-read'"]
    spark.sql(f"""CREATE TABLE {t} (id BIGINT, event_date DATE, customer_id INT, category STRING,
                  status STRING, amount DECIMAL(12,2), qty INT, ts TIMESTAMP, note STRING)
                  USING iceberg TBLPROPERTIES ({', '.join(props)})""")


def append_all_arms(table, cls_name, idx, batch_no, files, rpf):
    df = batch_df(cls_name, idx, batch_no, files, rpf).cache()
    n = df.count()
    for arm in ARMS:
        t0 = time.time()
        df.writeTo(fq(arm, table)).append()
        emit("commit", arm=arm, table=table, op="append", batch=batch_no, files=files, rows=n,
             seconds=round(time.time() - t0, 2))
    df.unpersist()


def health(arm, table):
    t = fq(arm, table)
    r = spark.sql(f"""SELECT
        sum(CASE WHEN content = 0 THEN 1 ELSE 0 END),
        sum(CASE WHEN content = 0 AND file_size_in_bytes < {SMALL_FILE_BYTES} THEN 1 ELSE 0 END),
        sum(CASE WHEN content <> 0 THEN 1 ELSE 0 END),
        sum(CASE WHEN content = 0 THEN file_size_in_bytes ELSE 0 END),
        sum(CASE WHEN content <> 0 THEN file_size_in_bytes ELSE 0 END),
        sum(CASE WHEN content = 0 THEN record_count ELSE 0 END)
        FROM {t}.files""").collect()[0]
    snaps = spark.sql(f"SELECT count(*) FROM {t}.snapshots").collect()[0][0]
    manifests = spark.sql(f"SELECT count(*) FROM {t}.manifests").collect()[0][0]
    data_files, small, deletes = int(r[0] or 0), int(r[1] or 0), int(r[2] or 0)
    return {"data_files": data_files, "small_files": small, "delete_files": deletes,
            "data_bytes": int(r[3] or 0), "delete_bytes": int(r[4] or 0), "data_records": int(r[5] or 0),
            "snapshots": snaps, "manifests": manifests,
            "healthy": small <= HT_MAX_SMALL and deletes <= HT_MAX_DELETE_FILES}


def gen():
    for table in TABLES:
        cls_name, idx = table.rsplit("_", 1)
        idx = int(idx)
        ic, ifpc, _, _, rpf = CLASSES[cls_name]
        for arm in ARMS:
            create_table(arm, table, cls_name)
        for c in range(ic):
            append_all_arms(table, cls_name, idx, c, ifpc, rpf)
    for arm in ARMS:
        for table in TABLES:
            emit("pre_health", arm=arm, table=table, **health(arm, table))


def ingest():
    for table in TABLES:
        cls_name, idx = table.rsplit("_", 1)
        idx = int(idx)
        _, _, cc, cfpc, rpf = CLASSES[cls_name]
        for k in range(cc):
            append_all_arms(table, cls_name, idx, 1000 * CYCLE + k, cfpc, rpf)
        if cls_name == "updates":
            for k in range(UPDATE_COMMITS_PER_CYCLE):
                residue = (CYCLE * UPDATE_COMMITS_PER_CYCLE + k) % 997
                for arm in ARMS:
                    t0 = time.time()
                    spark.sql(f"UPDATE {fq(arm, table)} SET amount = amount + 1, status = 'updated' "
                              f"WHERE id % 997 = {residue}")
                    emit("commit", arm=arm, table=table, op="update", residue=residue,
                         seconds=round(time.time() - t0, 2))
    for arm in ARMS:
        for table in TABLES:
            emit("pre_health", arm=arm, table=table, **health(arm, table))


def call(sql):
    t0 = time.time()
    rows = spark.sql(sql).collect()
    return [r.asDict(recursive=True) for r in rows], round(time.time() - t0, 3)


def diy():
    arm = os.environ["DIY_ARM"]
    assert arm in ("a1", "a2")
    window_t0 = time.time()
    emit("window_start", arm=arm)
    for table in TABLES:
        t = fq(arm, table)
        before = health(arm, table)
        emit("decision_input", arm=arm, table=table, **before)
        ops = []
        if arm == "a1":
            ops.append(("rewrite_data_files", f"CALL bench.system.rewrite_data_files(table => '{t}')"))
            ops.append(("rewrite_manifests", f"CALL bench.system.rewrite_manifests('{t}')"))
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
            ops.append(("expire_snapshots", f"CALL bench.system.expire_snapshots(table => '{t}', older_than => TIMESTAMP '{cutoff}', retain_last => 1)"))
        else:
            if before["small_files"] > HT_MAX_SMALL or before["delete_files"] > HT_MAX_DELETE_FILES:
                opts = ["'target-file-size-bytes','268435456'", "'min-input-files','2'"]
                if before["delete_files"] > HT_MAX_DELETE_FILES:
                    opts += ["'delete-file-threshold','1'", "'remove-dangling-deletes','true'"]
                ops.append(("rewrite_data_files", f"CALL bench.system.rewrite_data_files(table => '{t}', strategy => 'binpack', options => map({', '.join(opts)}))"))
            if before["manifests"] > 50:
                ops.append(("rewrite_manifests", f"CALL bench.system.rewrite_manifests('{t}')"))
        if not ops:
            emit("skip", arm=arm, table=table, reason="within health targets")
        for name, sql in ops:
            try:
                rows, secs = call(sql)
                emit("op", arm=arm, table=table, op=name, seconds=secs, result=rows, ok=True)
            except Exception as exc:
                emit("op", arm=arm, table=table, op=name, ok=False, error=f"{type(exc).__name__}: {str(exc)[:400]}")
        emit("post_health", arm=arm, table=table, **health(arm, table))
    emit("window_end", arm=arm, seconds=round(time.time() - window_t0, 2))


{"gen": gen, "ingest": ingest, "diy": diy}[MODE]()
spark.stop()
