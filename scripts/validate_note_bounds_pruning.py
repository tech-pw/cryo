#!/usr/bin/env python3
"""Create a partitioned table with Hindi `note` column, rewrite via Go, verify
$files bounds + Trino/Spark file pruning."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine-go" / "bin" / "cryo-engine-go"
TRINO = "http://localhost:8080"
CATALOG = "iceberg_rest"
SCHEMA = "cryo_utf8"
TABLE = "notes_prune"
FQN = f"{CATALOG}.{SCHEMA}.{TABLE}"
FILES = f'{CATALOG}.{SCHEMA}."{TABLE}$files"'

# Disjoint Unicode ranges so Iceberg file metrics can prune.
# Low partition: Devanagari starting near U+0905 (अ)
# High partition: Devanagari starting near U+0939 (ह)
NOTE_LOW = [
    "अतिरिक्त पाठ सामग्री परीक्षण के लिए लंबा हिंदी वाक्य एक दो तीन चार पांच",
    "अनुच्छेद लेखन अभ्यास प्रश्नोत्तरी सामग्री अतिरिक्त विवरण सहित विस्तृत नोट",
    "अंतरिक्ष प्रौद्योगिकी भाग दो कक्षा नोट्स जेईई हिंदी श्रृंखला",
]
NOTE_HIGH = [
    "हिंदी साहित्य प्रयोगवाद तथा नई कविता धारा कक्षा नोट्स विस्तृत विवरण",
    "हिमालय भूगोल महासागरीय निक्षेप दैनिक कक्षा नोट्स अतिरिक्त पाठ",
    "होमवर्क समाकलन अनुप्रयोग साधारण वक्र क्षेत्रफल अभ्यास प्रश्न",
]
MATCH_NOTE = NOTE_LOW[0]  # equality filter that only hits low partition
MISS_NOTE = "ज़ज़ज़ यह नोट किसी भी फ़ाइल में नहीं है और काट-छांट से बाहर है"


def trino(sql: str) -> dict:
    req = urllib.request.Request(
        f"{TRINO}/v1/statement",
        data=sql.encode("utf-8"),
        headers={
            "X-Trino-User": "cryo",
            "X-Trino-Catalog": CATALOG,
            "X-Trino-Schema": SCHEMA,
            "Content-Type": "text/plain; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        payload = json.loads(resp.read().decode())
    rows: list[list] = []
    columns = [c["name"] for c in payload.get("columns", [])]
    stats: dict = {}
    while True:
        rows.extend(payload.get("data") or [])
        if payload.get("stats"):
            stats = payload["stats"]
        if payload.get("error"):
            raise RuntimeError(json.dumps(payload["error"], ensure_ascii=False))
        nxt = payload.get("nextUri")
        if not nxt:
            break
        with urllib.request.urlopen(nxt, timeout=180) as resp:
            payload = json.loads(resp.read().decode())
        if not columns and payload.get("columns"):
            columns = [c["name"] for c in payload["columns"]]
        if payload.get("stats"):
            stats = payload["stats"]
    return {"columns": columns, "rows": rows, "stats": stats}


def sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def wait_trino() -> None:
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"{TRINO}/v1/info", timeout=3) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(2)
    raise RuntimeError("Trino not ready")


def build_engine() -> None:
    go_mod = REPO / "engine-go" / "go.mod"
    patched_mod = REPO / "engine-go" / "third_party" / "iceberg-go-v0.6.0-patched" / "go.mod"
    original = go_mod.read_text()
    patched_original = patched_mod.read_text()
    try:
        go_mod.write_text(original.replace("go 1.25.5", "go 1.25.1"))
        patched_mod.write_text(patched_original.replace("go 1.25.5", "go 1.25.1"))
        if not ENGINE.exists():
            subprocess.run(
                ["go", "build", "-o", "bin/cryo-engine-go", "./cmd/cryo-engine-go"],
                cwd=REPO / "engine-go",
                env={**os.environ, "CGO_ENABLED": "0", "GOTOOLCHAIN": "local"},
                check=True,
            )
    finally:
        go_mod.write_text(original)
        patched_mod.write_text(patched_original)


def go_rewrite() -> dict:
    payload = {
        "table_ref": f"{SCHEMA}.{TABLE}",
        "catalog_uri": "http://localhost:8181",
        "catalog_type": "rest",
        "warehouse": "s3://warehouse/",
        "mode": "standard",
        "s3_endpoint": "http://localhost:9000",
        "s3_access_key": "minioadmin",
        "s3_secret_key": "minioadmin",
        "catalog_properties": {
            "s3.endpoint": "http://localhost:9000",
            "s3.access-key-id": "minioadmin",
            "s3.secret-access-key": "minioadmin",
            "s3.path-style-access": "true",
            "s3.region": "us-east-1",
        },
        "operations": [
            {
                "type": "rewrite_data_files",
                "params": {
                    "target_file_size_bytes": 134217728,
                    "min_input_files": 2,
                    "metrics_mode": "truncate(16)",
                },
            }
        ],
    }
    proc = subprocess.run(
        [str(ENGINE), "execute"],
        input=json.dumps(payload).encode(),
        capture_output=True,
        cwd=REPO,
    )
    out = proc.stdout.decode()
    print(out[-1500:])
    if proc.returncode != 0:
        print(proc.stderr.decode()[-1500:], file=sys.stderr)
        raise RuntimeError(f"rewrite failed rc={proc.returncode}")
    # last JSON line
    for line in reversed(out.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    return {}


def print_note_bounds() -> list[dict]:
    res = trino(
        f"""
        SELECT file_path,
               partition,
               record_count,
               json_extract_scalar(readable_metrics, '$.note.lower_bound') AS note_lower,
               json_extract_scalar(readable_metrics, '$.note.upper_bound') AS note_upper
        FROM {FILES}
        ORDER BY 1
        """
    )
    print("== note lower/upper bounds from $files ==")
    print("columns:", res["columns"])
    files = []
    for row in res["rows"]:
        item = dict(zip(res["columns"], row, strict=True))
        files.append(item)
        print(json.dumps(item, ensure_ascii=False))
    return files


def trino_explain(label: str, where: str) -> str:
    sql = f"EXPLAIN (TYPE LOGICAL, FORMAT TEXT) SELECT count(*) FROM {FQN} WHERE {where}"
    res = trino(sql)
    text = "\n".join(r[0] for r in res["rows"])
    print(f"\n== Trino EXPLAIN [{label}] ==")
    print(text)
    return text


def trino_analyze(label: str, where: str) -> dict:
    sql = f"EXPLAIN ANALYZE SELECT count(*) FROM {FQN} WHERE {where}"
    res = trino(sql)
    text = "\n".join(r[0] for r in res["rows"])
    print(f"\n== Trino EXPLAIN ANALYZE [{label}] ==")
    # Keep the Iceberg/scan-relevant lines
    keep = []
    for line in text.splitlines():
        low = line.lower()
        if any(
            k in low
            for k in (
                "scan",
                "iceberg",
                "input",
                "physical",
                "raw input",
                "processed",
                "filter",
                "splits",
                "data size",
                "rows",
                "cpu",
            )
        ):
            keep.append(line)
    print("\n".join(keep[:80] if keep else text.splitlines()[:40]))
    return {"text": text, "stats": res.get("stats") or {}}


def spark_prune_check() -> None:
    print("\n== Spark pruning check ==")
    # Start tools-profile spark if needed, then run spark-sql against REST via HMS
    # Spark image uses HMS; our table is on REST. Use spark-submit with iceberg-spark
    # REST catalog conf if container is up.
    compose = REPO / "docker-compose.dev-datalake.yml"
    subprocess.run(
        ["docker", "compose", "-f", str(compose), "--profile", "tools", "up", "-d", "spark"],
        check=False,
        cwd=REPO,
    )
    time.sleep(3)
    # PySpark via container: query REST catalog table
    pyspark = r'''
from pyspark.sql import SparkSession
spark = (
  SparkSession.builder
  .appName("note-prune")
  .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
  .config("spark.sql.catalog.rest", "org.apache.iceberg.spark.SparkCatalog")
  .config("spark.sql.catalog.rest.type", "rest")
  .config("spark.sql.catalog.rest.uri", "http://iceberg-rest:8181")
  .config("spark.sql.catalog.rest.warehouse", "s3://warehouse/")
  .config("spark.sql.catalog.rest.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
  .config("spark.sql.catalog.rest.s3.endpoint", "http://minio:9000")
  .config("spark.sql.catalog.rest.s3.path-style-access", "true")
  .config("spark.sql.catalog.rest.s3.access-key-id", "minioadmin")
  .config("spark.sql.catalog.rest.s3.secret-access-key", "minioadmin")
  .config("spark.hadoop.fs.s3a.endpoint", "http://minio:9000")
  .config("spark.hadoop.fs.s3a.path.style.access", "true")
  .config("spark.hadoop.fs.s3a.access.key", "minioadmin")
  .config("spark.hadoop.fs.s3a.secret.key", "minioadmin")
  .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
  .getOrCreate()
)
spark.sparkContext.setLogLevel("WARN")
t = "rest.cryo_utf8.notes_prune"
print("FILES:")
spark.sql(f"SELECT file_path, readable_metrics FROM {t}.files").show(truncate=False)
note = """''' + MATCH_NOTE.replace('"', '\\"') + r'''"""
miss = """''' + MISS_NOTE.replace('"', '\\"') + r'''"""
print("EXPLAIN match:")
spark.sql(f"EXPLAIN EXTENDED SELECT count(*) FROM {t} WHERE note = '{note}'").show(truncate=False, n=200)
print("EXPLAIN miss:")
spark.sql(f"EXPLAIN EXTENDED SELECT count(*) FROM {t} WHERE note = '{miss}'").show(truncate=False, n=200)
# Collect metrics via Spark listener-less approach: count + files metadata join sanity
c1 = spark.sql(f"SELECT count(*) AS c FROM {t} WHERE note = '{note}'").collect()[0][0]
c0 = spark.sql(f"SELECT count(*) AS c FROM {t} WHERE note = '{miss}'").collect()[0][0]
print("match_count", c1, "miss_count", c0)
# Try to read metrics for note bounds (this is where UTF-8 decode failed before)
rows = spark.sql(f"""
SELECT file_path,
  readable_metrics['note'].lower_bound AS note_lower,
  readable_metrics['note'].upper_bound AS note_upper
FROM {t}.files
""").collect()
for r in rows:
    print("spark_bounds", r.file_path, r.note_lower, r.note_upper)
print("SPARK_OK")
spark.stop()
'''
    script_path = REPO / "tmp" / "spark_note_prune.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(pyspark)
    cmd = [
        "docker",
        "compose",
        "-f",
        str(compose),
        "--profile",
        "tools",
        "exec",
        "-T",
        "spark",
        "spark-submit",
        "--master",
        "local[2]",
        "/workspace/tmp/spark_note_prune.py",
    ]
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    print(proc.stdout[-4000:])
    if proc.returncode != 0:
        print(proc.stderr[-4000:], file=sys.stderr)
        print("SPARK_FAIL", proc.returncode)
    else:
        print("SPARK_DONE")


def main() -> int:
    wait_trino()
    build_engine()

    print("== create partitioned notes table ==")
    trino(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
    trino(f"DROP TABLE IF EXISTS {FQN}")
    trino(
        f"""
        CREATE TABLE {FQN} (
          id varchar,
          note varchar,
          grp varchar
        )
        WITH (
          format = 'PARQUET',
          format_version = 2,
          compression_codec = 'ZSTD',
          partitioning = ARRAY['grp'],
          location = 's3://warehouse/cryo_utf8/notes_prune'
        )
        """
    )

    # Multiple small files per partition so rewrite still emits one file per grp.
    print("== seed low/high note partitions ==")
    for i, note in enumerate(NOTE_LOW):
        trino(
            f"INSERT INTO {FQN} VALUES ({sql_str(f'L{i}')}, {sql_str(note)}, 'low')"
        )
    for i, note in enumerate(NOTE_HIGH):
        trino(
            f"INSERT INTO {FQN} VALUES ({sql_str(f'H{i}')}, {sql_str(note)}, 'high')"
        )
    before = trino(f"SELECT count(*) FROM {FILES}")
    print("files before rewrite:", before["rows"])

    print("== Go rewrite with truncate(16) metrics ==")
    result = go_rewrite()
    print("rewrite result summary:", json.dumps(result.get("results", []), ensure_ascii=False))

    files = print_note_bounds()
    if len(files) < 2:
        print("WARN: expected >=2 files for pruning demo; got", len(files))

    # Best filter: equality on note that exists only in low partition.
    where_match = f"note = {sql_str(MATCH_NOTE)}"
    where_miss = f"note = {sql_str(MISS_NOTE)}"
    where_all = "note IS NOT NULL"

    trino_explain("match-low-only", where_match)
    trino_explain("miss-all", where_miss)
    trino_explain("all-notes", where_all)

    m = trino_analyze("match-low-only", where_match)
    a = trino_analyze("all-notes", where_all)
    miss = trino_analyze("miss-all", where_miss)

    # Sanity counts
    c_match = trino(f"SELECT count(*) FROM {FQN} WHERE {where_match}")["rows"][0][0]
    c_miss = trino(f"SELECT count(*) FROM {FQN} WHERE {where_miss}")["rows"][0][0]
    c_all = trino(f"SELECT count(*) FROM {FQN} WHERE {where_all}")["rows"][0][0]
    print("\n== counts ==")
    print({"match": c_match, "miss": c_miss, "all": c_all})

    # Decode check: if bounds invalid UTF-8, this already failed above.
    print("\n== pruning verdict (Trino) ==")
    print(
        "note bounds are readable; use equality on note (disjoint partition ranges)."
    )
    print(
        "Compare EXPLAIN ANALYZE input sizes: match should read less than all if pruning works."
    )

    try:
        spark_prune_check()
    except Exception as exc:  # noqa: BLE001
        print(f"Spark check skipped/failed: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
