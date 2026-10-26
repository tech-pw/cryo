"""Read-only S3 usage of the benchmark run prefix, per top-level warehouse namespace.

Lists only s3://<benchmark-bucket>/ and emits
`S3U {...}` per namespace directory (objects, bytes, oldest/newest mtime). Never writes.
"""
import json, os
from collections import defaultdict
from pyarrow import fs

PREFIX = "<benchmark-bucket>/cryo-benchmarks/20261020-spark-001/"
s3 = fs.S3FileSystem(access_key=os.environ["AWS_ACCESS_KEY_ID"], secret_key=os.environ["AWS_SECRET_ACCESS_KEY"],
                     region="<region>")
agg = defaultdict(lambda: {"objects": 0, "bytes": 0, "min_mtime": None, "max_mtime": None})
for info in s3.get_file_info(fs.FileSelector(PREFIX, recursive=True)):
    if info.type != fs.FileType.File:
        continue
    rel = info.path[len(PREFIX):]
    parts = rel.split("/")
    key = "/".join(parts[:2]) if parts[0] == "warehouse" and len(parts) > 2 else parts[0]
    a = agg[key]
    a["objects"] += 1
    a["bytes"] += info.size
    m = info.mtime.timestamp() if info.mtime else None
    if m:
        a["min_mtime"] = m if a["min_mtime"] is None else min(a["min_mtime"], m)
        a["max_mtime"] = m if a["max_mtime"] is None else max(a["max_mtime"], m)
for k, v in sorted(agg.items()):
    print("S3U " + json.dumps({"prefix": k, **v}), flush=True)
print("S3U_TOTAL " + json.dumps({"objects": sum(v["objects"] for v in agg.values()),
                                 "bytes": sum(v["bytes"] for v in agg.values())}), flush=True)
