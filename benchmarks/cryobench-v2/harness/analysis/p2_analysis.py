"""Phase 2 (100 GiB table, target-size policy) and phase 6 (foreground protection) analysis.

Scored trials 2, 3, 4 (trial 1 was invalidated by the Cryo database loss). Outputs
results/v2/analysis/p2_*.csv, p6_*.csv, p2_summary.json.
"""
import statistics
from collections import Counter, defaultdict
from common import load, geo_ci, mean_ci, pct, pods, cryo_spark_pods, write_csv, write_json

TRIALS = [2, 3, 4]
ARMS = ["a0", "c256", "c128", "c512", "d256"]
P2_DEST = "51b7cb0a-dd76-4730-a2fd-066c4a2793c5"

tm = [r for r in load("tm") if r.get("schema") in {f"v2p2_r{t}" for t in TRIALS}]
ctrl = [r for r in load("ctrl") if r.get("_src") == "v2p2-scored-b.log"]
p2 = [r for r in load("p2") if r.get("trial") in TRIALS]
snaps = [r for r in load("snap") if r["table"].split(".")[0] in {f"v2p2_r{t}" for t in TRIALS}]
fg = [r for r in load("fg") if r.get("_src") == "v2p2-scored-b.log"]

# ---- table state and integrity
health = {(int(r["phase"][3]), r["phase"][4:], r["table"]): r for r in tm if r["kind"] == "health"}
state = []
for t in TRIALS:
    a0 = health[(t, "after", "a0")]
    for arm in ARMS:
        h = health[(t, "after", arm)]
        state.append({"trial": t, "arm": arm, "data_files": h["data_files"], "small_files_lt32MiB": h["small_files"],
                      "delete_files": h["delete_files"], "data_gib": h["data_bytes"] / 2**30,
                      "avg_file_mib": h["data_bytes"] / max(1, h["data_files"]) / 2**20,
                      "manifests": h["manifests"], "snapshots": h["snapshots"],
                      "fingerprint_equals_a0": (h["rows"], h["fp_id"], h["sum_amount"], h["fp_row"]) ==
                                               (a0["rows"], a0["fp_id"], a0["sum_amount"], a0["fp_row"])})
write_csv("p2_table_state.csv", state)

# ---- queries
med = defaultdict(list)
hashes = defaultdict(set)
for r in tm:
    if r["kind"] == "query" and r.get("scored") and r.get("ok"):
        t = int(r["phase"][3])
        when = r["phase"][4:]
        med[(t, when, r["table"], r["query"])].append(r["elapsed_ms"])
        hashes[(t, when, r["query"])].add(r.get("result_sha256"))
failed_q = sum(1 for r in tm if r["kind"] == "query" and r.get("scored") and not r.get("ok"))
med = {k: statistics.median(v) for k, v in med.items()}
queries = sorted({k[3] for k in med})
speed = []
for qq in queries:
    for arm in ARMS[1:]:
        per = [med[(t, "after", "a0", qq)] / med[(t, "after", arm, qq)] for t in TRIALS]
        g = geo_ci(per)
        speed.append({"query": qq, "arm": arm, "speedup_vs_a0": g["geomean"], "ci_lo": g["lo"], "ci_hi": g["hi"],
                      "per_trial": [round(x, 3) for x in per],
                      "a0_median_ms": statistics.median(med[(t, "after", "a0", qq)] for t in TRIALS),
                      "arm_median_ms": statistics.median(med[(t, "after", arm, qq)] for t in TRIALS)})
    for a, b in (("c128", "c256"), ("c512", "c256"), ("d256", "c256")):
        per = [med[(t, "after", b, qq)] / med[(t, "after", a, qq)] for t in TRIALS]
        g = geo_ci(per)
        speed.append({"query": qq, "arm": a, "vs": b, "speedup_vs_ref": g["geomean"], "ci_lo": g["lo"], "ci_hi": g["hi"],
                      "per_trial": [round(x, 3) for x in per]})
write_csv("p2_query_speedup.csv", speed)
all_q_geo = {arm: geo_ci([geo_ci([med[(t, "after", "a0", qq)] / med[(t, "after", arm, qq)] for qq in queries])["geomean"]
                          for t in TRIALS]) for arm in ARMS[1:]}

# ---- maintenance cost
allp = pods()
cp = cryo_spark_pods(allp, P2_DEST)
run_win = {}
for r in ctrl:
    if r.get("event") == "p2_run_submitted":
        run_win[(r["trial"], r["arm"])] = {"t0": r["t"], "run_id": r["run_id"]}
    if r.get("event") == "p2_run_done":
        run_win[(r["trial"], r["arm"])].update(t1=r["t"], seconds=r["seconds"], states=r["states"])
diy = {r["trial"]: r for r in ctrl if r.get("event") == "p2_diy_done"}
by_table = defaultdict(list)
for s in snaps:
    by_table[s["table"]].append(s)
maint = []
for t in TRIALS:
    for arm in ARMS[1:]:
        tbl = f"v2p2_r{t}.{arm}"
        if arm == "d256":
            sel = [p for p in allp if p["labels"].get("job-name") == f"v2p2-r{t}-d256"]
            secs = diy[t]["seconds"]
            t0 = min(p["t0"] for p in sel)
            t1 = max(p["t1"] for p in sel)
        else:
            w = run_win[(t, arm)]
            sel = [p for p in cp if w["t0"] - 5 <= p["t0"] <= w["t1"] + 5]
            secs, t0, t1 = w["seconds"], w["t0"], w["t1"]
        commits = [s for s in by_table[tbl] if s["summary"].get("operation") == "replace" and t0 * 1000 - 5000 <= s["ts_ms"] <= t1 * 1000 + 5000]
        sm = lambda key: sum(int(s["summary"].get(key, 0) or 0) for s in commits)
        maint.append({"trial": t, "arm": arm, "wall_s": secs, "vcpu_h": sum(p["vcpu_h"] for p in sel),
                      "gib_h": sum(p["gib_h"] for p in sel), "pods": len(sel),
                      "executors": sum(1 for p in sel if "-exec-" in p["name"]),
                      "commits": len(commits), "gib_rewritten": sm("removed-files-size") / 2**30,
                      "data_files_removed": sm("deleted-data-files"), "data_files_added": sm("added-data-files"),
                      "delete_files_removed": sm("removed-delete-files")})
write_csv("p2_maintenance.csv", maint)
maint_summary = {arm: {k: mean_ci([m[k] for m in maint if m["arm"] == arm]) for k in
                       ("wall_s", "vcpu_h", "gib_h", "gib_rewritten", "commits", "data_files_removed", "data_files_added", "executors")}
                 for arm in ARMS[1:]}

# ---- phase 6: foreground latency during maintenance
fgrows = []
for kind in ("baseline", "c128", "c256", "c512", "d256"):
    for qq in ("fg_day", "fg_week", "fg_point"):
        xs = [r["elapsed_ms"] for r in fg if r["label"].endswith("_" + kind) and r["query"] == qq and r["ok"]]
        per_trial_p95 = [pct([r["elapsed_ms"] for r in fg if r["label"] == f"r{t}_{kind}" and r["query"] == qq and r["ok"]], 0.95)
                         for t in TRIALS]
        fgrows.append({"window": kind, "query": qq, "n": len(xs), "p50": pct(xs, 0.5), "p95": pct(xs, 0.95),
                       "p99": pct(xs, 0.99), "per_trial_p95": per_trial_p95,
                       "failures": sum(1 for r in fg if r["label"].endswith("_" + kind) and r["query"] == qq and not r["ok"])})
for r in fgrows:
    base = next(b for b in fgrows if b["window"] == "baseline" and b["query"] == r["query"])
    for k in ("p50", "p95", "p99"):
        r[f"{k}_vs_baseline"] = r[k] / base[k] if r[k] and base[k] else None
write_csv("p6_foreground.csv", fgrows)

summary = {
    "trials": TRIALS,
    "integrity": {"fingerprint_all_equal_a0": all(s["fingerprint_equals_a0"] for s in state),
                  "result_hash_sets_with_more_than_one_value": [list(k) for k, v in hashes.items() if len(v) > 1],
                  "scored_query_failures": failed_q,
                  "cryo_job_states": dict(Counter(st for (t, a), w in run_win.items() for st in w["states"])),
                  "before_equals_a0_after": all(health[(t, "before", "a0")]["fp_id"] == health[(t, "after", "a0")]["fp_id"] for t in TRIALS)},
    "table_state_mean": {arm: {k: mean_ci([s[k] for s in state if s["arm"] == arm]) for k in
                               ("data_files", "small_files_lt32MiB", "delete_files", "avg_file_mib")} for arm in ARMS},
    "all_query_geomean_speedup_vs_a0": all_q_geo,
    "maintenance": maint_summary,
    "foreground": fgrows,
}
write_json("p2_summary.json", summary)
import json
print(json.dumps({k: summary[k] for k in ("integrity", "table_state_mean", "all_query_geomean_speedup_vs_a0", "maintenance")}, indent=1, default=lambda x: round(x, 3) if isinstance(x, float) else str(x)))
for s in speed:
    if "speedup_vs_a0" in s:
        print(s["query"], s["arm"], round(s["speedup_vs_a0"], 2), round(s["ci_lo"], 2), round(s["ci_hi"], 2), s["a0_median_ms"], s["arm_median_ms"])
for r in fgrows:
    print(r["window"], r["query"], r["n"], r["p50"], r["p95"], r["p99"], r["failures"], r.get("p95_vs_baseline"))
