"""CryoBench V2 phase 4: real registered Iceberg tables for fleet-scale control-plane tests.

Creates N tables in namespace v2p4_n{N} through the same REST catalog Cryo scans.
Ground-truth classes, deterministic by table index:
  healthy     1 data file
  fragmented  12 small data files, 12 snapshots   (below Cryo's planner default of 128)
  needle      130 small data files, 130 snapshots (above it; Cryo should plan optimize)
NEEDLES needle tables per fleet, evenly spaced; the rest alternate healthy / fragmented.
Emits `FX {...}` per table with its ground truth, and a summary line.
"""
import json, os, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import pyarrow as pa
from pyiceberg.catalog.rest import RestCatalog
from pyiceberg.schema import Schema
from pyiceberg.types import NestedField, LongType, StringType, DoubleType

WAREHOUSE = "s3://<benchmark-bucket>/warehouse/"
N = int(os.environ["FLEET_N"])
NEEDLES = int(os.environ.get("NEEDLES", "5"))
THREADS = int(os.environ.get("THREADS", "16"))
# gc.enabled=false makes Cryo's planner skip expire_snapshots/remove_orphan_files, which it
# otherwise plans (and dispatches as a Spark job) for every table on every standard run.
GC_ENABLED = os.environ.get("GC_ENABLED", "false")
NS = f"v2p4_n{N}" + ("" if GC_ENABLED == "false" else "_gc")

cat = RestCatalog("bench", **{
    "uri": "http://iceberg-rest.cryo-bench.svc:8181",
    "warehouse": WAREHOUSE,
    "s3.access-key-id": os.environ["AWS_ACCESS_KEY_ID"],
    "s3.secret-access-key": os.environ["AWS_SECRET_ACCESS_KEY"],
    "s3.region": "<region>",
})
schema = Schema(NestedField(1, "id", LongType(), required=False),
                NestedField(2, "name", StringType(), required=False),
                NestedField(3, "value", DoubleType(), required=False))
needle_idx = {int(i * N / NEEDLES) + N // (2 * NEEDLES) for i in range(NEEDLES)}


def klass(i):
    if i in needle_idx:
        return "needle", 130
    return ("healthy", 1) if i % 2 == 0 else ("fragmented", 12)


def make(i):
    name = f"t{i:05d}"
    k, files = klass(i)
    t0 = time.time()
    for attempt in range(6):
        try:
            try:
                tbl = cat.load_table((NS, name))
                existing = len(tbl.inspect.files())
                if existing == files:
                    return {"table": name, "class": k, "files": files, "seconds": 0, "reused": True}
                raise RuntimeError(f"{NS}.{name} exists with {existing} files, expected {files}")
            except Exception as exc:
                if "NoSuchTable" not in type(exc).__name__ and "does not exist" not in str(exc):
                    raise
            tbl = cat.create_table((NS, name), schema=schema, properties={"gc.enabled": GC_ENABLED})
            with tbl.transaction() as tx:
                for f in range(files):
                    tx.append(pa.table({"id": pa.array([i * 1000 + f], pa.int64()),
                                        "name": pa.array([f"{name}-{f}"]),
                                        "value": pa.array([float(f)])}))
            return {"table": name, "class": k, "files": files, "seconds": round(time.time() - t0, 3),
                    "attempts": attempt + 1}
        except Exception as exc:
            last = f"{type(exc).__name__}: {str(exc)[:200]}"
            time.sleep(2 * (attempt + 1))
    return {"table": name, "class": k, "files": files, "error": last}


try:
    cat.create_namespace(NS)
except Exception as exc:
    if "AlreadyExists" not in type(exc).__name__:
        raise
t0 = time.time()
ok = err = 0
with ThreadPoolExecutor(THREADS) as ex:
    for fut in as_completed([ex.submit(make, i) for i in range(N)]):
        r = fut.result()
        ok += "error" not in r
        err += "error" in r
        print("FX " + json.dumps(dict(r, namespace=NS)), flush=True)
print("FX_SUMMARY " + json.dumps({"namespace": NS, "n": N, "ok": ok, "errors": err, "needles": sorted(needle_idx),
                                   "seconds": round(time.time() - t0, 1)}), flush=True)
