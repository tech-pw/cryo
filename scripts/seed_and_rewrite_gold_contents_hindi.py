#!/usr/bin/env python3
"""Seed a local gold_contents clone with Hindi titles, rewrite via cryo-engine-go, verify $files."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "engine-go" / "bin" / "cryo-engine-go"
TRINO = "http://localhost:8080"
CATALOG = "iceberg_rest"
SCHEMA = "cdp_revenue_v2"
TABLE = "gold_contents"
FQN = f"{CATALOG}.{SCHEMA}.{TABLE}"

# Real Hindi titles sampled from prod cdp.cdp_revenue_v2.gold_contents (mcp-trino).
HINDI_ROWS = [
    ("66a9f5760000670018b3ce4e", "द्रव्यमान केंद्र : DPP 03 (Of Lec 04) || ( Jee Hindi 2024)", "66a9f576e426680018ebaef8", "Active", "Concept", "द्रव्यमान केंद्र : DPP 03 (Of Lec 04) || ( Jee Hindi 2024)", "द्रव्यमान-केंद्र---dpp-03--of-lec-04--||---jee-hindi-2024--910846"),
    ("66a9f715482bf900187c159f", "उपसहसंयोजक यौगिक 05 : संयोजी बंध सिद्धान्त || NO DPP", "66a9f714e4d0f20018b7feb8", "Active", "Lecture", "उपसहसंयोजक यौगिक 05 : संयोजी बंध सिद्धान्त || NO DPP", "उपसहसंयोजक-यौगिक-05---संयोजी-बंध-सिद्धान्त-||-no-dpp-539227"),
    ("66a9fa4ba03bd00018e57e13", "त्रिकोणमिति : DPP 05 (Of Lec 07) || ( Jee Hindi 2024)", "66a9fa4ab1d2c600181a752a", "Active", "Concept", "त्रिकोणमिति : DPP 05 (Of Lec 07) || ( Jee Hindi 2024)", "त्रिकोणमिति---dpp-05--of-lec-07--||---jee-hindi-2024--220347"),
    ("66d584e2e02edf001775411f", "तंत्रिकीय नियंत्रण एवं समन्वय : DPP 01 (MCQ Quiz)", "66d58457d1900c00182a9524", "Active", "Exercise", None, "तंत्रिकीय-नियंत्रण-एवं-समन्वय---dpp-01--mcq-quiz--248500"),
    ("6718e66c8d9557ba845bfd56", "कुछ आधारभूत सिद्धांत तथा तकनीकें (नामकरण) : DPP 03 (MCQ Quiz)", "671747834de552078967a3d4", "Inactive", "Exercise", None, "कुछ-आधारभूत-सिद्धांत-तथा-तकनीकें--नामकरण----dpp-03--mcq-quiz--609909"),
    ("6724fd3d52d813ac486974ee", "Ch 02 : भारत का भौतिक विन्यास : DPP (MCQ Quiz)", "6724fc1db0a3773c437d8168", "Active", "Exercise", None, "ch-02---भारत-का-भौतिक-विन्यास---dpp--mcq-quiz--079665"),
    ("672dbca46b089c467f5d4530", "हिंदी साहित्य : प्रयोगवाद तथा नई कविता धारा || Class Notes", "672dbc9f71a560f61f0ec758", "Active", "Concept", "हिंदी साहित्य : प्रयोगवाद तथा नई कविता धारा || Class Notes", "हिंदी-साहित्य---प्रयोगवाद-तथा-नई-कविता-धारा-||-class-notes-370904"),
    ("672dbe3c86032c62013f4efa", "भूगोल : महासागरीय निक्षेप || Daily Class Notes", "672dbe3c1d3bcb8f8a65eae8", "Active", "Concept", "भूगोल : महासागरीय निक्षेप || Daily Class Notes", "भूगोल---महासागरीय-निक्षेप-||-daily-class-notes-320434"),
    ("672dc07672484420e89f5b32", "भूगोल :भू-आकृति विज्ञान Part 3 || Class Notes", "672dc0725a81031ff3cddd5a", "Active", "Concept", "भूगोल :भू-आकृति विज्ञान Part 3 || Class Notes", "भूगोल--भू-आकृति-विज्ञान-part-3-||-class-notes-182668"),
    ("6856e8ee8c690fbed27d261c", "विज्ञान एवं प्रौद्योगिकी 02 : अंतरिक्ष प्रौद्योगिकी (Part 02)", "6856e8dace75c260128cb449", "Active", "Lecture", "विज्ञान एवं प्रौद्योगिकी 02 : अंतरिक्ष प्रौद्योगिकी (Part 02)", "विज्ञान-एवं-प्रौद्योगिकी-02---अंतरिक्ष-प्रौद्योगिकी--part-02--750948"),
    ("6835cd21c67e5697ea14a684", "साप्ताहिक करेंट अफेयर्स (1 फरवरी - 7 फरवरी 2025)", None, "Inactive", None, None, None),
    ("69808a195119ba7c46461406", "पुष्पीय पादपों में लैंगिक जनन 04 :  नरयुग्मकोद्भीद का विकास || गुरुबीजाणु धानी || गुरुबीजाणु जनन", "69808a17088af27d2714002e", "Active", "Lecture", "पुष्पीय पादपों में लैंगिक जनन 04 :  नरयुग्मकोद्भीद का विकास || गुरुबीजाणु धानी || गुरुबीजाणु जनन", "पुष्पीय-पादपों-में-लैंगिक-जनन-04---नरयुग्मकोद्भीद-का-विकास-||-गुरुबीजाणु-धानी-||-गुरुबीजाणु-जनन-550098"),
]


def trino(sql: str) -> dict:
    body = sql.encode("utf-8")
    req = urllib.request.Request(
        f"{TRINO}/v1/statement",
        data=body,
        headers={
            "X-Trino-User": "cryo",
            "X-Trino-Catalog": CATALOG,
            "X-Trino-Schema": SCHEMA,
            "Content-Type": "text/plain; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode())
    rows: list[list] = []
    columns = [c["name"] for c in payload.get("columns", [])]
    while True:
        rows.extend(payload.get("data") or [])
        if payload.get("error"):
            raise RuntimeError(json.dumps(payload["error"], ensure_ascii=False))
        nxt = payload.get("nextUri")
        if not nxt:
            break
        with urllib.request.urlopen(nxt, timeout=120) as resp:
            payload = json.loads(resp.read().decode())
        if not columns and payload.get("columns"):
            columns = [c["name"] for c in payload["columns"]]
    return {"columns": columns, "rows": rows}


def sql_str(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {FQN} (
   _id varchar,
   title varchar,
   topicid varchar,
   status varchar,
   content ROW(
     \"exerciseId\" varchar, \"_id\" varchar, text varchar, \"videoUrl\" varchar,
     \"videoPlaylistUrl\" varchar, \"fileId\" varchar, \"imageId\" varchar, \"videoId\" varchar,
     \"videoType\" varchar, status varchar, \"watchTime\" varchar, \"flashTime\" varchar,
     timeline array(varchar), \"pdfPageCount\" double, \"audioId\" varchar
   ),
   type varchar,
   __v integer,
   createdat timestamp(6) with time zone,
   __deleted boolean,
   __source_ts_ms bigint,
   fileid varchar,
   _schema_version integer,
   partition integer,
   offset bigint,
   kafka_event_timestamp timestamp(6) with time zone,
   price double,
   fileurl varchar,
   displayorder double,
   availablefor array(varchar),
   commentcount double,
   isavailablefrompoints boolean,
   updatedat timestamp(6) with time zone,
   actions array(varchar),
   islive boolean,
   ratingcount double,
   restrictcontent boolean,
   restrictioncount double,
   sharecount double,
   upvotecount double,
   level varchar,
   chapterid varchar,
   isimportant boolean,
   organizationid varchar,
   programid varchar,
   slug varchar,
   subjectid varchar,
   isfreeforsamsung boolean,
   subtopicid varchar,
   hassubjectivecontent boolean,
   isclinicalcornerenabled boolean,
   isfarreforconceptenabled boolean,
   publishdate timestamp(6) with time zone,
   imageid varchar,
   teachers array(varchar),
   batchtype ROW(value varchar, name varchar),
   coursetype ROW(value varchar, name varchar),
   year ROW(value varchar, name varchar),
   isfreeforsamsug boolean,
   relatedcontents array(varchar),
   expireat timestamp(6) with time zone,
   launchat timestamp(6) with time zone,
   oldurl varchar,
   updatedfrom varchar,
   ismigrated boolean,
   oldsubjectid varchar,
   oldchapterid varchar,
   oldtopicid varchar,
   oldsubtopicid varchar,
   oldcontentid varchar,
   smartdpp boolean,
   uid varchar,
   contenttype varchar,
   subtitle varchar,
   thumbnaillink varchar,
   videolink varchar,
   filters varchar,
   widgetid varchar,
   name varchar,
   language varchar,
   pdflink varchar,
   pdfdownload varchar,
   ingested_at bigint,
   cdp_source_name varchar,
   ingest_date varchar,
   contentid varchar,
   content_exerciseid varchar,
   content_fileid varchar,
   content_text varchar,
   content_videotype varchar,
   content_videourl varchar,
   cdp_ingested_at timestamp(6) with time zone,
   cdp_ind integer,
   description varchar,
   popularscore double,
   testid varchar,
   ts_ms double,
   viewcount double
)
WITH (
   compression_codec = 'ZSTD',
   format = 'PARQUET',
   format_version = 2,
   location = 's3://warehouse/cdp_revenue_v2/gold_contents'
)
"""


def main() -> int:
    # Wait for Trino
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"{TRINO}/v1/info", timeout=3) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(2)
    else:
        print("Trino not ready", file=sys.stderr)
        return 1

    print("== create schema/table ==")
    trino(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
    try:
        trino(f"DROP TABLE IF EXISTS {FQN}")
    except Exception as exc:  # noqa: BLE001
        print(f"drop skipped: {exc}")
    trino(CREATE_SQL)

    print("== seed Hindi rows (one INSERT per file for rewrite) ==")
    for i, (_id, title, topicid, status, typ, content_text, slug) in enumerate(HINDI_ROWS):
        # Pad title so code-point length >> 16 (exercises truncate(16) metrics path).
        padded = title + " || अतिरिक्त हिंदी पाठ सामग्री परीक्षण " + ("क" * 8)
        sql = f"""
        INSERT INTO {FQN} (_id, title, topicid, status, type, content_text, slug, language, cdp_source_name, ingest_date)
        VALUES (
          {sql_str(_id + f'-{i}')},
          {sql_str(padded)},
          {sql_str(topicid)},
          {sql_str(status)},
          {sql_str(typ)},
          {sql_str(content_text or padded)},
          {sql_str(slug)},
          'Hindi',
          's3',
          '2026-08-12'
        )
        """
        trino(sql)
        print(f"  inserted file-ish row {i+1}/{len(HINDI_ROWS)}")

    before = trino(f'SELECT count(*) AS n FROM {CATALOG}.{SCHEMA}."{TABLE}$files"')
    print("files before rewrite:", before["rows"])

    print("== build cryo-engine-go + rewrite ==")
    go_mod = REPO / "engine-go" / "go.mod"
    patched_mod = REPO / "engine-go" / "third_party" / "iceberg-go-v0.6.0-patched" / "go.mod"
    original = go_mod.read_text()
    patched_original = patched_mod.read_text()
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
    try:
        # Host go is 1.25.1; module pins 1.25.5 — temporarily relax for local build.
        go_mod.write_text(original.replace("go 1.25.5", "go 1.25.1"))
        patched_mod.write_text(patched_original.replace("go 1.25.5", "go 1.25.1"))
        subprocess.run(
            ["go", "build", "-o", "bin/cryo-engine-go", "./cmd/cryo-engine-go"],
            cwd=REPO / "engine-go",
            env={**os.environ, "CGO_ENABLED": "0", "GOTOOLCHAIN": "local"},
            check=True,
        )
        proc = subprocess.run(
            [str(ENGINE), "execute"],
            input=json.dumps(payload).encode(),
            capture_output=True,
            cwd=REPO,
        )
    finally:
        go_mod.write_text(original)
        patched_mod.write_text(patched_original)

    print(proc.stdout.decode()[-2000:])
    if proc.stderr:
        print("stderr:", proc.stderr.decode()[-2000:], file=sys.stderr)
    if proc.returncode != 0:
        print(f"rewrite failed rc={proc.returncode}", file=sys.stderr)
        return proc.returncode

    print("== $files readable_metrics via Trino ==")
    after = trino(f'SELECT count(*) AS n FROM {CATALOG}.{SCHEMA}."{TABLE}$files"')
    print("files after rewrite:", after["rows"])
    metrics = trino(
        f"""
        SELECT file_path,
               json_extract_scalar(readable_metrics, '$.title.lower_bound') AS title_lower,
               json_extract_scalar(readable_metrics, '$.title.upper_bound') AS title_upper,
               json_extract_scalar(readable_metrics, '$.content_text.lower_bound') AS content_lower,
               json_extract_scalar(readable_metrics, '$.content_text.upper_bound') AS content_upper
        FROM {CATALOG}.{SCHEMA}."{TABLE}$files"
        """
    )
    print("columns:", metrics["columns"])
    for row in metrics["rows"]:
        print(json.dumps(row, ensure_ascii=False))
    print("OK: Trino decoded title/content_text bounds without UTF-8 failure")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
