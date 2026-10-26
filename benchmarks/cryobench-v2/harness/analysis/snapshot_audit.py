"""Read-only Iceberg snapshot audit for scored V2 tables.

For every table in the namespaces matching PREFIXES, emits one `SNAP {...}` line per snapshot
(id, parent, timestamp, operation, full summary). Bytes rewritten per maintenance commit come
from the summary (`removed-files-size`, `deleted-data-files`, `removed-delete-files`), the same
way for every arm, because Cryo's Spark-on-K8s jobs report no file metrics (upstream #155).
Metadata reads only; nothing is written.
"""
import json, os
from pyiceberg.catalog.rest import RestCatalog

PREFIXES = tuple(os.environ.get("PREFIXES", "v2p1_r7_,v2p1_r8_,v2p1_r9_,v2p2_r2,v2p2_r3,v2p2_r4,v2p3_r3_").split(","))
cat = RestCatalog("bench", **{
    "uri": "http://iceberg-rest.cryo-bench.svc:8181",
    "warehouse": "s3://<benchmark-bucket>/warehouse/",
    "s3.access-key-id": os.environ["AWS_ACCESS_KEY_ID"],
    "s3.secret-access-key": os.environ["AWS_SECRET_ACCESS_KEY"],
    "s3.region": "ap-south-1",
})

for (ns,) in sorted(cat.list_namespaces()):
    if not ns.startswith(PREFIXES):
        continue
    for ident in sorted(cat.list_tables(ns)):
        try:
            t = cat.load_table(ident)
        except Exception as exc:
            print("SNAP_ERR " + json.dumps({"table": ".".join(ident), "error": str(exc)[:300]}), flush=True)
            continue
        md = t.metadata
        current = md.current_snapshot_id
        for s in md.snapshots:
            summ = s.summary.model_dump() if s.summary is not None else {}
            print("SNAP " + json.dumps({
                "table": ".".join(ident), "snapshot_id": s.snapshot_id, "parent_id": s.parent_snapshot_id,
                "ts_ms": s.timestamp_ms, "current": s.snapshot_id == current, "summary": summ,
            }, default=str), flush=True)
print("SNAP_DONE {}", flush=True)
