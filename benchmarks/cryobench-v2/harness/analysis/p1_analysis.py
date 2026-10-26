"""Phase 1 analysis: autonomous Cryo (A3) vs scheduled Spark (A1 naive, A2 candidate) vs none (A0).

Implements the pre-registered analysis in CRYOBENCH_V2_PROTOCOL.md (phase 1) for scored trials
7, 8 and 9 (trials 1-6 were aborted or invalidated; see the protocol amendments).
Outputs results/v2/analysis/p1_*.csv and p1_summary.json.
"""
import statistics
from collections import Counter, defaultdict
from common import load, mean_ci, geo_ci, pods, cryo_spark_pods, write_csv, write_json

TRIALS = [7, 8, 9]
CYCLES = [1, 2, 3, 4]
ARMS = ["a0", "a1", "a2", "a3"]
TABLES = [f"{c}_{i}" for c in ("healthy", "moderate", "severe", "updates", "lowtraffic") for i in (1, 2)]
HT_SMALL, HT_DELETE = 16, 8
QO = {"q_agg": 1500, "q_filter": 1000, "q_point": 1000}
SLA_WINDOW_S = 1200
P1_DEST = "d0a22bbc-0236-4992-9a5a-4b852e50fc10"
CP_PREFIXES = ("cryo-bench-api", "cryo-bench-governor", "cryo-bench-scanner-worker", "cryo-bench-worker",
               "cryo-postgres", "cryo-redis")


def healthy(h):
    return h["small_files"] <= HT_SMALL and h["delete_files"] <= HT_DELETE


def cls(table):
    return table.rsplit("_", 1)[0]


p1 = [r for r in load("p1") if r.get("trial") in TRIALS]
tm = [r for r in load("tm") if str(r.get("phase", "")).startswith(("r7c", "r8c", "r9c"))
      and str(r.get("schema", "")).startswith("v2p1_")]
ctrl = [r for r in load("ctrl") if r.get("_src") == "v2p1-scored-c.log"]
snaps = [r for r in load("snap") if r["table"].startswith(("v2p1_r7_", "v2p1_r8_", "v2p1_r9_"))]

# decision-time health: after ingestion, before any window
dec = {(r["trial"], r["cycle"], r["arm"], r["table"]): r for r in p1 if r["kind"] == "pre_health" and r["mode"] == "ingest"}
# post-cycle health, same Trino $files query for every arm
after = {}
for r in tm:
    if r["kind"] == "health" and r.get("ok"):
        t, c = int(r["phase"][1]), int(r["phase"][3:])
        after[(t, c, r["schema"].split("_")[-1], r["table"])] = r
# cross-check: DIY post_health (Spark) equals Trino health for A1/A2
xcheck = Counter()
for r in p1:
    if r["kind"] == "post_health":
        a = after.get((r["trial"], r["cycle"], r["arm"], r["table"]))
        xcheck["match" if a and (a["data_files"], a["small_files"], a["delete_files"]) ==
               (r["data_files"], r["small_files"], r["delete_files"]) else "mismatch"] += 1

windows = {}
for r in ctrl:
    if r.get("event") == "window_start":
        windows.setdefault((r["trial"], r["cycle"], r["arm"]), {})["t0"] = r["t"]
    elif r.get("event") == "window_end":
        w = windows.setdefault((r["trial"], r["cycle"], r["arm"]), {})
        w.update(t1=r["t"], seconds=r["seconds"], scan_s=r.get("scan_seconds"), run_s=r.get("run_seconds"),
                 run_id=r.get("run_id"), run_status=r.get("run_status"))
run_to_window = {w["run_id"]: k for k, w in windows.items() if w.get("run_id")}
run_submit_t = {r["run_id"]: r["t"] for r in ctrl if r.get("event") == "cryo_run_submitted"}

# snapshot chains: proves whether any expire removed anything
by_table = defaultdict(list)
for s in snaps:
    by_table[s["table"]].append(s)
expire_removed = {}
for tbl, ss in by_table.items():
    ids = {s["snapshot_id"] for s in ss}
    roots = [s for s in ss if s["parent_id"] is None]
    broken = [s for s in ss if s["parent_id"] is not None and s["parent_id"] not in ids]
    expire_removed[tbl] = bool(broken) or len(roots) != 1


def window_commits(trial, cycle, arm, table):
    w = windows.get((trial, cycle, arm))
    if not w or "t0" not in w:
        return []
    return [s for s in by_table.get(f"v2p1_r{trial}_{arm}.{table}", [])
            if w["t0"] * 1000 - 5000 <= s["ts_ms"] <= w["t1"] * 1000 + 5000]


def removed_bytes(s):
    return int(s["summary"].get("removed-files-size", 0) or 0)


# ---- operations, per arm
ops = []
for r in p1:
    if r["kind"] != "op" or r["arm"] not in ("a1", "a2"):
        continue
    d = dec[(r["trial"], r["cycle"], r["arm"], r["table"])]
    res = (r.get("result") or [{}])[0] if r.get("ok") else {}
    if r["op"] == "rewrite_data_files":
        changed = (res.get("rewritten_data_files_count", 0) or 0) + (res.get("removed_delete_files_count", 0) or 0) > 0
        rewritten = res.get("rewritten_bytes_count", 0) or 0
    elif r["op"] == "rewrite_manifests":
        changed, rewritten = (res.get("rewritten_manifests_count", 0) or 0) > 0, 0
    else:
        changed, rewritten = any((v or 0) > 0 for v in res.values()), 0
    ops.append({"trial": r["trial"], "cycle": r["cycle"], "arm": r["arm"], "table": r["table"], "class": cls(r["table"]),
                "op": r["op"], "ok": r.get("ok"), "healthy_at_decision": healthy(d), "changed": changed,
                "bytes_rewritten_reported": rewritten, "spark_apps": 0})
cryo_jobs = []
for r in ctrl:
    if r.get("event") != "cryo_job_detail":
        continue
    j = r["job"]
    trial = int(j["schema_name"].split("_")[1][1:])
    _, cycle, _ = run_to_window[j["run_id"]]
    d = dec[(trial, cycle, "a3", j["table_name"])]
    commits = window_commits(trial, cycle, "a3", j["table_name"])
    data_commits = [s for s in commits if s["summary"].get("operation") in ("replace", "overwrite", "delete")]
    cryo_jobs.append({**j, "trial": trial, "cycle": cycle})
    for o in j["plan"]["operations"]:
        if o["type"] == "expire_snapshots":
            changed = expire_removed[f"v2p1_r{trial}_a3.{j['table_name']}"]
        else:
            changed = bool(data_commits)
        ops.append({"trial": trial, "cycle": cycle, "arm": "a3", "table": j["table_name"], "class": cls(j["table_name"]),
                    "op": o["type"], "ok": j["status"] == "succeeded", "healthy_at_decision": healthy(d),
                    "changed": changed, "bytes_rewritten_reported": None, "spark_apps": 1})
for o in ops:
    o["necessary"] = not o["healthy_at_decision"]
    o["unnecessary"] = o["healthy_at_decision"] or not o["changed"]
write_csv("p1_operations.csv", ops)

# ---- compute
allp = pods()
cryo_p = cryo_spark_pods(allp, P1_DEST)
cp_req = {}
for p in allp:
    if p["name"].startswith(CP_PREFIXES):
        base = p["name"].rsplit("-", 2)[0]
        cp_req.setdefault(p["name"], (base, p["req_cpu"], p["req_mem"] / 2**30))
cp_vcpu_deployed = sum(v[1] for k, v in cp_req.items() if k in {p["name"] for p in allp if p["t1"] > 1792656900 and p["t0"] < 1792677354})
compute = {}
for (t, c, arm), w in windows.items():
    if t not in TRIALS or "t0" not in w:
        continue
    if arm == "a3":
        sel = [p for p in cryo_p if w["t0"] - 5 <= p["t0"] <= w["t1"] + 5]
        spark_jobs = len({p["k8s_job"] for p in sel})
    else:
        sel = [p for p in allp if p["labels"].get("job-name") == f"v2p1-r{t}-c{c}-{arm}"]
        spark_jobs = 1 if sel else 0
    compute[(t, c, arm)] = {"vcpu_h": sum(p["vcpu_h"] for p in sel), "gib_h": sum(p["gib_h"] for p in sel),
                            "pods": len(sel), "spark_apps": spark_jobs, "peak_vcpu": None}
    # peak concurrent requested vCPU
    ev = sorted([(p["t0"], p["req_cpu"]) for p in sel] + [(p["t1"], -p["req_cpu"]) for p in sel])
    cur = peak = 0.0
    for _, dv in ev:
        cur += dv
        peak = max(peak, cur)
    compute[(t, c, arm)]["peak_vcpu"] = peak

# ---- per window table
rows = []
for t in TRIALS:
    for c in CYCLES:
        for arm in ARMS:
            w = windows.get((t, c, arm), {})
            o = [x for x in ops if (x["trial"], x["cycle"], x["arm"]) == (t, c, arm)]
            not_healthy = [tb for tb in TABLES if not healthy(dec[(t, c, arm, tb)])]
            after_unhealthy = [tb for tb in TABLES if not healthy(after[(t, c, arm, tb)])]
            restored = [tb for tb in not_healthy if healthy(after[(t, c, arm, tb)])]
            rb = sum(removed_bytes(s) for tb in TABLES for s in window_commits(t, c, arm, tb)
                     if s["summary"].get("operation") in ("replace", "overwrite", "delete")) if arm != "a0" else 0
            comp = compute.get((t, c, arm), {})
            rows.append({"trial": t, "cycle": c, "arm": arm, "window_s": w.get("seconds"),
                         "scan_s": w.get("scan_s"), "run_s": w.get("run_s"), "cryo_run_status": w.get("run_status"),
                         "ops": len(o), "ops_failed": sum(1 for x in o if not x["ok"]),
                         "necessary_ops": sum(x["necessary"] for x in o), "unnecessary_ops": sum(x["unnecessary"] for x in o),
                         "unnecessary_on_healthy": sum(x["healthy_at_decision"] for x in o),
                         "unnecessary_nochange": sum((not x["healthy_at_decision"]) and not x["changed"] for x in o),
                         "spark_apps": comp.get("spark_apps", 0), "tables_unhealthy_at_decision": len(not_healthy),
                         "restored": len(restored), "backlog": len(after_unhealthy),
                         "gib_rewritten": rb / 2**30, "vcpu_h": comp.get("vcpu_h", 0.0), "gib_h": comp.get("gib_h", 0.0),
                         "peak_vcpu": comp.get("peak_vcpu", 0.0)})
write_csv("p1_windows.csv", rows)

# ---- queries
q = defaultdict(list)
for r in tm:
    if r["kind"] == "query" and r.get("scored") and r.get("ok"):
        t, c = int(r["phase"][1]), int(r["phase"][3:])
        arm = r["schema"].split("_")[-1]
        q[(t, c, arm, r["table"], r["query"])].append(r["elapsed_ms"])
q_fail = sum(1 for r in tm if r["kind"] == "query" and r.get("scored") and not r.get("ok"))
qo_rows, qmed = [], {}
for (t, c, arm, tb, qq), xs in q.items():
    qmed[(t, c, arm, tb, qq)] = statistics.median(xs)
for t in TRIALS:
    for c in [0] + CYCLES:
        for arm in ARMS:
            for tb in TABLES:
                meds = {qq: qmed.get((t, c, arm, tb, qq)) for qq in QO}
                if any(v is None for v in meds.values()):
                    continue
                qo_rows.append({"trial": t, "cycle": c, "arm": arm, "table": tb, "class": cls(tb), **meds,
                                "qo_met": all(meds[qq] <= QO[qq] for qq in QO)})
write_csv("p1_query_medians.csv", qo_rows)


def class_median(t, c, arm, k, qq):
    xs = [x for tb in TABLES if cls(tb) == k for x in q.get((t, c, arm, tb, qq), [])]
    return statistics.median(xs) if xs else None


ratios = []
for k in sorted({cls(tb) for tb in TABLES}):
    for qq in QO:
        for arm, ref in (("a1", "a0"), ("a2", "a0"), ("a3", "a0"), ("a3", "a1"), ("a3", "a2")):
            per_trial = []
            for t in TRIALS:
                rs = [class_median(t, c, arm, k, qq) / class_median(t, c, ref, k, qq) for c in CYCLES
                      if class_median(t, c, arm, k, qq) and class_median(t, c, ref, k, qq)]
                per_trial.append(geo_ci(rs)["geomean"])
            g = geo_ci(per_trial)
            ratios.append({"class": k, "query": qq, "arm": arm, "vs": ref, "ratio_geomean": g["geomean"],
                           "ci_lo": g["lo"], "ci_hi": g["hi"], "n_trials": g["n"],
                           "per_trial": per_trial})
write_csv("p1_query_ratios.csv", ratios)

# ---- integrity / SLA
fp = {}
for (t, c, arm, tb), a in after.items():
    fp[(t, c, arm, tb)] = (a.get("rows"), a.get("fp_id"), a.get("sum_amount"), a.get("fp_row"))
fp_mismatch = [{"trial": t, "cycle": c, "arm": arm, "table": tb} for (t, c, arm, tb), v in fp.items()
               if arm != "a0" and v != fp.get((t, c, "a0", tb))]
ingest_ok = all(r.get("status") == "succeeded" for r in ctrl if r.get("event") == "job_done" and "-ingest" in r["job"])

# ---- per-trial pre-registered metrics
summary = {"trials": TRIALS, "cycles": CYCLES, "integrity": {
    "fingerprint_mismatches": fp_mismatch, "ingest_jobs_all_succeeded": ingest_ok, "scored_query_failures": q_fail,
    "diy_post_health_vs_trino_health": dict(xcheck), "any_snapshot_expired_by_any_arm": {
        arm: any(v for k, v in expire_removed.items() if k.split(".")[0].endswith(arm)) for arm in ARMS},
    "cryo_jobs": len(cryo_jobs), "cryo_job_status": dict(Counter(j["status"] for j in cryo_jobs)),
    "cryo_run_status_at_window_end": dict(Counter(w.get("run_status") for k, w in windows.items() if k[2] == "a3")),
}, "per_arm": {}, "headline": {}}
for arm in ARMS:
    R = [r for r in rows if r["arm"] == arm]
    per_trial = lambda f: [f([r for r in R if r["trial"] == t]) for t in TRIALS]
    qo = [x for x in qo_rows if x["arm"] == arm and x["cycle"] in CYCLES]
    summary["per_arm"][arm] = {
        "window_s_mean_per_trial": mean_ci(per_trial(lambda rr: statistics.fmean(r["window_s"] for r in rr)) if arm != "a0" else []),
        "window_s_max": max((r["window_s"] or 0) for r in R) if arm != "a0" else None,
        "ops_per_trial": mean_ci(per_trial(lambda rr: sum(r["ops"] for r in rr))),
        "spark_apps_per_trial": mean_ci(per_trial(lambda rr: sum(r["spark_apps"] for r in rr))),
        "unnecessary_ops_per_trial": mean_ci(per_trial(lambda rr: sum(r["unnecessary_ops"] for r in rr))),
        "necessary_ops_per_trial": mean_ci(per_trial(lambda rr: sum(r["necessary_ops"] for r in rr))),
        "gib_rewritten_per_trial": mean_ci(per_trial(lambda rr: sum(r["gib_rewritten"] for r in rr))),
        "vcpu_h_per_trial": mean_ci(per_trial(lambda rr: sum(r["vcpu_h"] for r in rr))),
        "gib_h_per_trial": mean_ci(per_trial(lambda rr: sum(r["gib_h"] for r in rr))),
        "peak_vcpu_max": max(r["peak_vcpu"] for r in R),
        "restored_pct": mean_ci(per_trial(lambda rr: 100 * sum(r["restored"] for r in rr) /
                                          max(1, sum(r["tables_unhealthy_at_decision"] for r in rr)))),
        "backlog_per_trial": mean_ci(per_trial(lambda rr: sum(r["backlog"] for r in rr))),
        "qo_compliance_pct": 100 * sum(x["qo_met"] for x in qo) / len(qo) if qo else None,
        "qo_n": len(qo),
        "sla_window_met_pct": 100 * sum((r["window_s"] or 0) <= SLA_WINDOW_S for r in R) / len(R) if arm != "a0" else None,
        "ops_failed": sum(r["ops_failed"] for r in R),
        "ops_by_type": dict(Counter(o["op"] for o in ops if o["arm"] == arm)),
        "unnecessary_by_type": dict(Counter(o["op"] for o in ops if o["arm"] == arm and o["unnecessary"])),
    }


def per_trial_metric(arm, key):
    return [sum(r[key] for r in rows if r["arm"] == arm and r["trial"] == t) for t in TRIALS]


ua1, ua3 = per_trial_metric("a1", "unnecessary_ops"), per_trial_metric("a3", "unnecessary_ops")
ca1, ca3 = per_trial_metric("a1", "vcpu_h"), per_trial_metric("a3", "vcpu_h")
ua2, ca2 = per_trial_metric("a2", "unnecessary_ops"), per_trial_metric("a2", "vcpu_h")
a3_window_h = [sum((r["window_s"] or 0) for r in rows if r["arm"] == "a3" and r["trial"] == t) / 3600 for t in TRIALS]
summary["headline"] = {
    "unnecessary_avoided_pct_vs_a1": mean_ci([100 * (a - b) / a for a, b in zip(ua1, ua3)]),
    "unnecessary_avoided_pct_vs_a2": mean_ci([100 * (a - b) / a if a else None for a, b in zip(ua2, ua3)]),
    "compute_saved_pct_vs_a1": mean_ci([100 * (1 - b / a) for a, b in zip(ca1, ca3)]),
    "compute_saved_pct_vs_a2": mean_ci([100 * (1 - b / a) for a, b in zip(ca2, ca3)]),
    "control_plane_vcpu_requested_deployed": cp_vcpu_deployed,
    "compute_saved_pct_vs_a1_with_cp_during_windows": mean_ci(
        [100 * (1 - (b + cp_vcpu_deployed * h) / a) for a, b, h in zip(ca1, ca3, a3_window_h)]),
    "per_trial": {"a1_unnecessary": ua1, "a2_unnecessary": ua2, "a3_unnecessary": ua3,
                  "a1_vcpu_h": ca1, "a2_vcpu_h": ca2, "a3_vcpu_h": ca3, "a3_window_h": a3_window_h},
}
# Sensitivity: snapshot expiry. With 7-day retention inside a ~2 h trial every expire is a no-op by
# construction; in daily production expiry is required. A2 has no expire at all. Report both views.
job_op = {p["labels"].get("job-name"): p["labels"].get("cryo-operation") for p in cryo_p
          if p["labels"].get("cryo-operation") and p["labels"].get("job-name")}
a3_by_op = defaultdict(lambda: defaultdict(float))
for (t, c, arm), w in windows.items():
    if arm != "a3" or t not in TRIALS:
        continue
    for p in cryo_p:
        if w["t0"] - 5 <= p["t0"] <= w["t1"] + 5:
            a3_by_op[t][p["labels"].get("cryo-operation") or job_op.get(p["k8s_job"], "?")] += p["vcpu_h"]
role = Counter()
for p in cryo_p:
    if any(w["t0"] - 5 <= p["t0"] <= w["t1"] + 5 for (t, c, arm), w in windows.items() if arm == "a3" and t in TRIALS):
        role["executor" if "-exec-" in p["name"] else "driver" if p["name"].endswith("-driver") else "submitter"] += p["vcpu_h"]
ca3_noexp = [sum(v for k, v in a3_by_op[t].items() if k != "expire_snapshots") for t in TRIALS]
un = lambda arm, t: sum(1 for o in ops if o["arm"] == arm and o["trial"] == t and o["unnecessary"] and o["op"] != "expire_snapshots")
summary["headline"]["excluding_expire"] = {
    "a1_unnecessary": [un("a1", t) for t in TRIALS], "a2_unnecessary": [un("a2", t) for t in TRIALS],
    "a3_unnecessary": [un("a3", t) for t in TRIALS],
    "unnecessary_avoided_pct_vs_a1": mean_ci([100 * (un("a1", t) - un("a3", t)) / un("a1", t) for t in TRIALS]),
    "a3_vcpu_h": ca3_noexp,
    "compute_saved_pct_vs_a1": mean_ci([100 * (1 - b / a) for a, b in zip(ca1, ca3_noexp)]),
    "compute_saved_pct_vs_a2": mean_ci([100 * (1 - b / a) for a, b in zip(ca2, ca3_noexp)]),
}
summary["a3_compute_by_operation_vcpu_h"] = {t: dict(v) for t, v in a3_by_op.items()}
summary["a3_compute_by_pod_role_vcpu_h_all_trials"] = dict(role)

# Cryo split
split = []
for j in cryo_jobs:
    from datetime import datetime
    ts = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None
    sub = run_submit_t.get(j["run_id"])
    split.append({"trial": j["trial"], "cycle": j["cycle"], "table": j["table_name"],
                  "ops": "+".join(o["type"] for o in j["plan"]["operations"]),
                  "queued_to_running_s": ts(j["started_at"]) - sub if sub and j["started_at"] else None,
                  "running_s": ts(j["finished_at"]) - ts(j["started_at"]) if j["started_at"] and j["finished_at"] else None})
write_csv("p1_cryo_jobs.csv", split)
summary["cryo_split"] = {
    "scan_s": mean_ci([r["scan_s"] for r in rows if r["arm"] == "a3"]),
    "queued_to_running_s_median": statistics.median([s["queued_to_running_s"] for s in split if s["queued_to_running_s"] is not None]),
    "job_running_s_median_by_ops": {k: statistics.median([s["running_s"] for s in split if s["ops"] == k])
                                    for k in sorted({s["ops"] for s in split})},
    "planner_skipped_optimize_tables": sum(1 for j in cryo_jobs for n in j["plan"].get("notes", []) if n.startswith("Skipped optimize")),
}
write_json("p1_summary.json", summary)
print(__import__("json").dumps({k: summary[k] for k in ("integrity", "headline", "cryo_split")}, indent=1, default=str))
for arm in ARMS:
    print(arm, __import__("json").dumps(summary["per_arm"][arm], default=str))
