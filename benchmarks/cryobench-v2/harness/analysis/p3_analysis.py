"""Phase 3 (continuous CDC) analysis, scored trial 3.

The stream job's per-batch records for batches 0-32 were lost to container log rotation (see
RESULTS); file counts over time are therefore taken from Iceberg snapshot summaries, which
record total data and delete files after every commit, for every arm alike.
"""
import statistics
from collections import Counter, defaultdict
from common import load, mean_ci, pods, cryo_spark_pods, write_csv, write_json

T = 3
ARMS = ["a0", "a1", "a3"]
P3_DEST = "d821c69e-3d33-4a1a-bd49-08b727c7d4d0"

snaps = [r for r in load("snap") if r["table"].startswith(f"v2p3_r{T}_")]
ctrl = [r for r in load("ctrl") if r.get("_src") == "v2p3-scored-c.log"]
p3 = [r for r in load("p3") if r.get("trial") == T]
tm = [r for r in load("tm") if str(r.get("schema", "")).startswith(f"v2p3_r{T}_")]

start = next(r["t"] for r in ctrl if r.get("event") == "p3_start")
end = next(r["t"] for r in ctrl if r.get("event") == "p3_done")

series, endstate = [], {}
for tbl in sorted({s["table"] for s in snaps}):
    ss = sorted((s for s in snaps if s["table"] == tbl), key=lambda s: s["ts_ms"])
    arm, name = tbl.split(".")[0].split("_")[-1], tbl.split(".")[1]
    ops = Counter(s["summary"].get("operation") for s in ss)
    pts = [(s["ts_ms"] / 1000, int(s["summary"].get("total-data-files", 0)), int(s["summary"].get("total-delete-files", 0)))
           for s in ss]
    for t, d, x in pts:
        series.append({"arm": arm, "table": name, "t_rel_s": round(t - start, 1), "data_files": d, "delete_files": x})
    # time-weighted mean over the stream window
    tw = {"data": 0.0, "delete": 0.0}
    span = 0.0
    for (t0, d, x), (t1, _, _) in zip(pts, pts[1:] + [(end, 0, 0)]):
        a, b = max(t0, start), min(t1, end)
        if b > a:
            tw["data"] += d * (b - a)
            tw["delete"] += x * (b - a)
            span += b - a
    maint = [s for s in ss if s["summary"].get("operation") == "replace"]
    endstate[(arm, name)] = {"arm": arm, "table": name, "snapshots": len(ss), "ops": dict(ops),
                             "maintenance_commits": len(maint),
                             "gib_rewritten": sum(int(s["summary"].get("removed-files-size", 0) or 0) for s in maint) / 2**30,
                             "final_data_files": pts[-1][1], "final_delete_files": pts[-1][2],
                             "max_data_files": max(p[1] for p in pts), "max_delete_files": max(p[2] for p in pts),
                             "tw_mean_data_files": tw["data"] / span if span else None,
                             "tw_mean_delete_files": tw["delete"] / span if span else None}
write_csv("p3_file_series.csv", series)
write_csv("p3_end_state.csv", list(endstate.values()))

# maintenance outcomes
a1_ops = [{"window": i // 6 + 1, **{k: r.get(k) for k in ("table", "op", "ok", "seconds")},
           "error": (r.get("error") or "")[:200]} for i, r in enumerate(r for r in p3 if r["kind"] == "op" and r["arm"] == "a1")]
cryo_jobs = [{"window": r["n"], "table": r["job"]["table_name"], "status": r["job"]["status"],
              "ops": "+".join(o["type"] for o in r["job"]["plan"]["operations"])}
             for r in ctrl if r.get("event") == "p3_cryo_job"]
a3_windows = [{"n": r["n"], "seconds": r["seconds"], "t": r["t"]} for r in ctrl if r.get("event") == "p3_window_end"]
a3_starts = {r["n"]: r["t"] for r in ctrl if r.get("event") == "p3_window_start"}
# did each Cryo job commit? replace snapshot on that table inside the window
for j in cryo_jobs:
    w = next(x for x in a3_windows if x["n"] == j["window"])
    t0 = a3_starts[j["window"]]
    j["committed"] = any(s["summary"].get("operation") == "replace" and t0 * 1000 <= s["ts_ms"] <= w["t"] * 1000 + 5000
                         for s in snaps if s["table"] == f"v2p3_r{T}_a3.{j['table']}")
write_csv("p3_a1_ops.csv", a1_ops)
write_csv("p3_cryo_jobs.csv", cryo_jobs)

# compute
allp = pods()
cp = [p for p in cryo_spark_pods(allp, P3_DEST) if start <= p["t0"] <= end + 600]
a1p = [p for p in allp if str(p["labels"].get("job-name", "")).startswith(f"v2p3-r{T}-a1-")]

# probes
probe = defaultdict(list)
for r in tm:
    if r["kind"] == "query" and r.get("scored") and r.get("ok"):
        arm = r["schema"].split("_")[-1]
        probe[(r["label"].split("_")[-1], arm, r["table"], r["query"])].append(r["elapsed_ms"])
probe_rows = [{"probe": k[0], "arm": k[1], "table": k[2], "query": k[3], "median_ms": statistics.median(v), "n": len(v)}
              for k, v in sorted(probe.items())]
write_csv("p3_probes.csv", probe_rows)
ratio = {}
for arm in ("a1", "a3"):
    for qq in ("c_agg", "c_point"):
        rs = [p["median_ms"] / q["median_ms"] for p in probe_rows if p["arm"] == arm and p["query"] == qq
              for q in probe_rows if q["arm"] == "a0" and q["query"] == qq and q["probe"] == p["probe"] and q["table"] == p["table"]]
        ratio[f"{arm}_{qq}_vs_a0_mean_over_probes"] = statistics.fmean(rs) if rs else None
        fin = [p["median_ms"] / q["median_ms"] for p in probe_rows if p["arm"] == arm and p["query"] == qq and p["probe"] == "final"
               for q in probe_rows if q["arm"] == "a0" and q["query"] == qq and q["probe"] == "final" and q["table"] == p["table"]]
        ratio[f"{arm}_{qq}_vs_a0_final"] = fin[0] if fin else None

fps = {r["arm"]: (r["rows"], r["fp"]) for r in p3 if r["kind"] == "fingerprint"}
summary = {
    "trial": T, "duration_s": end - start, "batches": next(r["batches"] for r in ctrl if r.get("event") == "p3_start"),
    "fingerprints_equal": len(set(fps.values())) == 1, "fingerprints": fps,
    "end_state": list(endstate.values()),
    "a1": {"windows": 3, "ops": len(a1_ops), "ops_failed": sum(1 for o in a1_ops if not o["ok"]),
           "failed_ops": [o for o in a1_ops if not o["ok"]],
           "vcpu_h": sum(p["vcpu_h"] for p in a1p), "pods": len(a1p)},
    "a3": {"windows": len(a3_windows), "window_s": mean_ci([w["seconds"] for w in a3_windows]),
           "jobs": len(cryo_jobs), "job_status": dict(Counter(j["status"] for j in cryo_jobs)),
           "jobs_committed": sum(j["committed"] for j in cryo_jobs),
           "ops": dict(Counter(o for j in cryo_jobs for o in j["ops"].split("+"))),
           "spark_apps": len({p["k8s_job"] for p in cp}), "vcpu_h": sum(p["vcpu_h"] for p in cp), "pods": len(cp)},
    "probe_ratios": ratio,
    "stream_commit_retries": "NOT_ESTABLISHED for batches 0-32 (stream log rotated); batches 33-35 in p3.jsonl",
}
write_json("p3_summary.json", summary)
import json
print(json.dumps(summary, indent=1, default=str))
