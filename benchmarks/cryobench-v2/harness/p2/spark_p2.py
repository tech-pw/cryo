"""CryoBench V2 phase 2: realistic partitioned table (~100 GiB target) and zero-copy arm clones.

MODE=gen    build bench.v2p2_r{TRIAL}.base: 60 daily partitions in three file-size bands
            (days 1-20: 4-8 MiB files, 21-40: 16-32 MiB, 41-60: 64-128 MiB), many commits,
            then merge-on-read DELETE and UPDATE so the table carries delete files.
            Also builds a small product dimension table for JOIN queries.
MODE=clone  register the base table's current metadata file as one table per arm. All arms
            start from byte-identical metadata. Clones share data files, so nothing may ever
            run expire_snapshots or remove_orphan_files on them (Cryo presets are optimize-only).

Scale knobs: TARGET_GIB (default 100). Bytes per row are measured from a calibration commit
and used to size the remaining commits, so the physical size is measured, not assumed.
"""
import json, math, os, time
from datetime import datetime, timezone
from pyspark.sql import SparkSession

WAREHOUSE = "s3://<benchmark-bucket>/warehouse/"
assert WAREHOUSE.startswith("s3://<benchmark-bucket>/")
MODE = os.environ["MODE"]
TRIAL = int(os.environ["TRIAL"])
SEED = 9000 + TRIAL
TARGET_GIB = float(os.environ.get("TARGET_GIB", "100"))
ARMS = os.environ.get("ARMS", "a0,c256,c128,c512,d256").split(",")
NS = f"bench.v2p2_r{TRIAL}"
BASE = f"{NS}.base"
DIM = f"{NS}.dim_product"
DAYS = 60
# band -> (first_day, last_day, target_file_mib, commits_per_day)
BANDS = [(0, 19, 6, 4), (20, 39, 24, 2), (40, 59, 96, 1)]

spark = (
    SparkSession.builder.appName(f"v2p2-{MODE}-r{TRIAL}")
    .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
    .config("spark.sql.catalog.bench", "org.apache.iceberg.spark.SparkCatalog")
    .config("spark.sql.catalog.bench.type", "rest")
    .config("spark.sql.catalog.bench.uri", "http://iceberg-rest.cryo-bench.svc:8181")
    .config("spark.sql.catalog.bench.warehouse", WAREHOUSE)
    .config("spark.sql.catalog.bench.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
    .config("spark.sql.catalog.bench.s3.access-key-id", os.environ["AWS_ACCESS_KEY_ID"])
    .config("spark.sql.catalog.bench.s3.secret-access-key", os.environ["AWS_SECRET_ACCESS_KEY"])
    .config("spark.sql.catalog.bench.client.region", "<region>")
    .config("spark.sql.catalog.bench.s3.retry.num-retries", "32")
    .config("spark.sql.adaptive.enabled", "false")
    .getOrCreate()
)


def emit(kind, **kw):
    kw.update(kind=kind, trial=TRIAL, mode=MODE, utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              t=round(time.time(), 3))
    print("P2 " + json.dumps(kw, default=str), flush=True)


def rows_sql(day, commit, files, rows_per_file):
    tag = f"{SEED}-{day}-{commit}"
    base = (day * 100 + commit) * 10**8
    h = lambda salt: f"xxhash64('{tag}', '{salt}', b.id, n)"
    return f"""
      SELECT CAST({base} + b.id * {rows_per_file} + n AS BIGINT) AS order_id,
             date_add(DATE'2026-01-01', {day}) AS event_date,
             CAST(timestamp_seconds(1767225600 + {day} * 86400 + pmod({h('ts')}, 86400)) AS TIMESTAMP) AS event_ts,
             pmod({h('cust')}, 2000000) AS customer_id,
             CAST(pmod({h('prod')}, 20000) AS INT) AS product_id,
             element_at(array('books','games','music','video','home','garden','toys','sport','food','auto','health','beauty',
                              'office','pets','baby','tools','shoes','bags','phones','laptops','tv','camera','jewel','travel'),
                        CAST(pmod({h('cat')}, 24) AS INT) + 1) AS category,
             element_at(array('north','south','east','west','central','ne','nw','se','sw','metro','rural','intl'),
                        CAST(pmod({h('reg')}, 12) AS INT) + 1) AS region,
             element_at(array('web','app','store','partner','test'), CAST(pmod({h('ch')}, 5) AS INT) + 1) AS channel,
             element_at(array('new','paid','shipped','delivered','cancelled','returned'), CAST(pmod({h('st')}, 6) AS INT) + 1) AS status,
             CAST(pmod({h('q')}, 9) + 1 AS INT) AS quantity,
             CAST(pmod({h('p')}, 500000) / 100.0 AS DECIMAL(10,2)) AS unit_price,
             CAST(pmod({h('d')}, 30) / 100.0 AS DECIMAL(5,2)) AS discount,
             CAST(pmod({h('a')}, 5000000) / 100.0 AS DECIMAL(12,2)) AS amount,
             element_at(array('INR','USD','EUR','GBP'), CAST(pmod({h('cur')}, 4) AS INT) + 1) AS currency,
             concat(element_at(array('android','ios','web','tv'), CAST(pmod({h('dev')}, 4) AS INT) + 1), '-',
                    CAST(pmod({h('dv')}, 300) AS STRING)) AS device,
             concat(lpad(hex({h('s1')}), 16, '0'), lpad(hex({h('s2')}), 16, '0')) AS session_id,
             concat_ws(' ', transform(sequence(0, CAST(pmod({h('len')}, 12) AS INT) + 4),
                       i -> element_at(array('fast','slow','great','bad','ok','late','box','gift','size','color','fit',
                                             'return','refund','again','never','love','hate','cheap','pricey','broken'),
                                       CAST(pmod(xxhash64('{tag}', 'w', b.id, n, i), 20) AS INT) + 1)),
                       lpad(hex({h('c')}), 16, '0')) AS comment
      FROM range(0, {files}, 1, {files}) b
      LATERAL VIEW explode(sequence(0, {rows_per_file - 1})) e AS n
    """


def table_files(t):
    r = spark.sql(f"""SELECT sum(IF(content=0,1,0)), sum(IF(content=0,file_size_in_bytes,0)),
                      sum(IF(content<>0,1,0)), sum(IF(content<>0,file_size_in_bytes,0)),
                      sum(IF(content=0,record_count,0)) FROM {t}.files""").collect()[0]
    return {"data_files": int(r[0] or 0), "data_bytes": int(r[1] or 0), "delete_files": int(r[2] or 0),
            "delete_bytes": int(r[3] or 0), "data_records": int(r[4] or 0)}


def gen():
    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {NS}")
    for name in ("base", "dim_product"):
        if spark.sql(f"SHOW TABLES IN {NS} LIKE '{name}'").count() > 0:
            raise SystemExit(f"refusing to overwrite {NS}.{name}")
    spark.sql(f"""CREATE TABLE {BASE} (order_id BIGINT, event_date DATE, event_ts TIMESTAMP, customer_id BIGINT,
        product_id INT, category STRING, region STRING, channel STRING, status STRING, quantity INT,
        unit_price DECIMAL(10,2), discount DECIMAL(5,2), amount DECIMAL(12,2), currency STRING, device STRING,
        session_id STRING, comment STRING) USING iceberg PARTITIONED BY (event_date)
        TBLPROPERTIES ('format-version'='2', 'write.distribution-mode'='none', 'write.parquet.compression-codec'='zstd',
                       'write.delete.mode'='merge-on-read', 'write.update.mode'='merge-on-read',
                       'write.merge.mode'='merge-on-read')""")
    # Calibration: one commit of day 0 to measure physical bytes per row.
    cal_rows = 60_000
    t0 = time.time()
    spark.sql(f"INSERT INTO {BASE} {rows_sql(0, 99, 4, cal_rows // 4)}")
    f = table_files(BASE)
    bpr = f["data_bytes"] / f["data_records"]
    emit("calibration", rows=f["data_records"], bytes=f["data_bytes"], bytes_per_row=round(bpr, 2),
         seconds=round(time.time() - t0, 1))
    per_day_bytes = TARGET_GIB * 2**30 / DAYS
    for first, last, file_mib, cpd in BANDS:
        rows_per_file = max(1000, int(file_mib * 2**20 / bpr))
        files_per_commit = max(1, int(math.ceil(per_day_bytes / (file_mib * 2**20) / cpd)))
        for day in range(first, last + 1):
            for c in range(cpd):
                tc = time.time()
                spark.sql(f"INSERT INTO {BASE} {rows_sql(day, c, files_per_commit, rows_per_file)}")
                emit("commit", day=day, commit=c, files=files_per_commit, rows_per_file=rows_per_file,
                     seconds=round(time.time() - tc, 1))
    emit("after_insert", **table_files(BASE))
    # Merge-on-read changes: cold-band delete of test traffic, hot-band status updates.
    tc = time.time()
    spark.sql(f"DELETE FROM {BASE} WHERE event_date BETWEEN DATE'2026-01-05' AND DATE'2026-01-07' AND channel = 'test'")
    emit("delete", seconds=round(time.time() - tc, 1), **table_files(BASE))
    for k in range(3):
        tc = time.time()
        spark.sql(f"UPDATE {BASE} SET status = 'refunded' WHERE event_date >= DATE'2026-02-20' AND pmod(order_id, 100) = {k}")
        emit("update", k=k, seconds=round(time.time() - tc, 1), **table_files(BASE))
    spark.sql(f"""CREATE TABLE {DIM} USING iceberg TBLPROPERTIES ('format-version'='2') AS
        SELECT CAST(id AS INT) AS product_id,
               element_at(array('acme','globex','initech','umbrella','stark','wayne','wonka','tyrell'), CAST(pmod(xxhash64('{SEED}', id), 8) AS INT) + 1) AS brand,
               CAST(pmod(xxhash64('{SEED}', 'm', id), 40) AS INT) AS margin_pct
        FROM range(0, 20000)""")
    fp = spark.sql(f"""SELECT count(*), CAST(sum(CAST(xxhash64(order_id, status, amount) AS DECIMAL(38,0))) AS STRING),
                       CAST(sum(amount) AS STRING) FROM {BASE}""").collect()[0]
    emit("base_done", rows=fp[0], fp_xxhash_sum=fp[1], sum_amount=fp[2],
         snapshots=spark.sql(f"SELECT count(*) FROM {BASE}.snapshots").collect()[0][0],
         manifests=spark.sql(f"SELECT count(*) FROM {BASE}.manifests").collect()[0][0], **table_files(BASE))


def clone():
    src = f"bench.v2p2_r{os.environ.get('BASE_TRIAL', TRIAL)}.base"
    meta = spark.sql(f"SELECT file FROM {src}.metadata_log_entries ORDER BY timestamp DESC LIMIT 1").collect()[0][0]
    assert meta.startswith(WAREHOUSE), meta
    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {NS}")
    for arm in ARMS:
        name = f"{NS}.{arm}"
        if spark.sql(f"SHOW TABLES IN {NS} LIKE '{arm}'").count() > 0:
            raise SystemExit(f"refusing to overwrite {name}")
        spark.sql(f"CALL bench.system.register_table(table => '{name}', metadata_file => '{meta}')")
        emit("clone", arm=arm, metadata_file=meta, **table_files(name))


def diy_sql():
    """Manual Spark arm: run the statements Cryo dispatched for c256, retargeted to the d256 clone."""
    import base64, re
    sql = base64.b64decode(os.environ["SQL_B64"]).decode()
    target = os.environ["DIY_TABLE"]
    assert target.startswith(f"{NS}.") and target.split(".")[-1] not in ("base", "a0"), target
    stmts = [s.strip() for s in sql.split(";\n") if s.strip()]
    for s in stmts:
        assert re.search(r"\bv2p2_r\d+\.c256\b", s), s[:200]
        s2 = re.sub(r"`?[\w-]+`?\.`?v2p2_r\d+`?\.`?c256`?", target, s)
        tc = time.time()
        rows = [r.asDict() for r in spark.sql(s2).collect()]
        emit("diy_stmt", sql=s2, seconds=round(time.time() - tc, 2), result=rows[:5])
    emit("diy_done", **table_files(target))


{"gen": gen, "clone": clone, "diy_sql": diy_sql}[MODE]()
spark.stop()
