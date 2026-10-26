"""Phase 5: cost ledger (V2), per-operation costs and break-even. Everything here is MODELED from
measured usage (pod requests x lifetime, Iceberg/S3 bytes, Trino counters) and the published-rate
assumptions below. Nothing is billed data: no Cost Explorer or CUR access in this environment.
"""
import json, statistics
from collections import defaultdict
from common import load, pods, write_json, write_csv, OUT, ROOT

# Rate assumptions (USD, ap-south-1). Frozen in the protocol budget section before the runs.
RATE_VCPU_H = 0.057          # r6a on-demand per vCPU-hour (r6a nodes observed; 8 GiB per vCPU covers memory)
RATE_S3_GB_MONTH = 0.025     # S3 Standard storage
RATE_S3_PUT_1K = 0.005       # PUT/COPY/POST/LIST per 1,000
RATE_S3_GET_1K = 0.0004      # GET/SELECT per 1,000
RATE_NAT_GB = 0.056          # NAT gateway data processing, worst case if S3 traffic is not via an endpoint
V2_START = 1792609200        # 2026-10-21 19:00 UTC, first V2 pod

allp = [p for p in pods() if p["t1"] > V2_START]


def category(p):
    n, lab = p["name"], p["labels"]
    if n.startswith("bench-trino"):
        return "trino"
    if n.startswith(("cryo-bench-api", "cryo-bench-worker", "cryo-bench-governor", "cryo-bench-scanner-worker",
                     "cryo-postgres", "cryo-redis", "cryo-bench-scheduler")):
        return "cryo_control_plane"
    if n.startswith("cryo-bench-scanner-"):
        return "cryo_scanner_cronjob"
    if n.startswith(("cryo-spark-", "cryo-maintenance-")):
        return "cryo_spark"
    if n.startswith("iceberg-rest") or n.startswith("bench-registry"):
        return "shared_catalog_registry"
    jn = str(lab.get("job-name", ""))
    if jn.startswith(("v2p1-", "v2p2-", "v2p3-")) and (jn.endswith(("-a1", "-a2", "-d256")) or "-a1-w" in jn):
        return "diy_spark"
    if "-gen" in jn or "-ingest" in jn or "-clone" in jn or "-stream" in jn or "-init" in jn or "-fx-" in jn:
        return "data_generation"
    if "measure" in jn or "-before" in jn or "-after" in jn or "-probe" in jn or "-fg-" in jn:
        return "measurement"
    return "harness_other"


ledger = defaultdict(lambda: {"vcpu_h": 0.0, "gib_h": 0.0, "pods": 0})
for p in allp:
    t0 = max(p["t0"], V2_START)
    h = (p["t1"] - t0) / 3600
    c = ledger[category(p)]
    c["vcpu_h"] += p["req_cpu"] * h
    c["gib_h"] += p["req_mem"] / 2**30 * h
    c["pods"] += 1
for c in ledger.values():
    c["usd_modeled"] = c["vcpu_h"] * RATE_VCPU_H
compute_usd = sum(c["usd_modeled"] for c in ledger.values())

# S3 storage: current bytes in the run prefix, held for the V2 duration so far (upper bound)
s3 = [json.loads(l.split(" ", 1)[1]) for l in (ROOT / "results/v2/raw/v2-s3usage.log").open() if l.startswith("S3U_TOTAL")][0]
v2_days = (max(p["t1"] for p in allp) - V2_START) / 86400
storage_usd = s3["bytes"] / 1e9 * RATE_S3_GB_MONTH * v2_days / 30
# S3 requests: Trino measured (client-side counter, all GET-class), Spark/pyiceberg modeled from objects written
tm, fg = load("tm"), load("fg")
trino_reqs = sum(r.get("s3_requests") or 0 for r in tm if r["kind"] == "query") + sum(r.get("s3_requests") or 0 for r in fg)
trino_bytes = sum(r.get("physical_input_bytes") or 0 for r in tm if r["kind"] == "query")
objects_written = s3["objects"]
requests_usd = trino_reqs / 1000 * RATE_S3_GET_1K + objects_written * 3 / 1000 * RATE_S3_PUT_1K
# NAT worst case: Trino bytes read + Spark rewrite read+write (from snapshot summaries) + generation writes
snaps = load("snap")
rewrite_bytes = sum(int(s["summary"].get("removed-files-size", 0) or 0) + int(s["summary"].get("added-files-size", 0) or 0)
                    for s in snaps if s["summary"].get("operation") == "replace")
nat_gb = (trino_bytes + rewrite_bytes + s3["bytes"]) / 1e9
nat_usd_worst = nat_gb * RATE_NAT_GB

# Idle Trino overnight (no scored work between phase 2 end and phase 4 start)
idle = {"from_utc": "2026-10-22T18:57:18Z", "to_utc": "2026-10-23T06:54:00Z", "hours": (1792738440 - 1792695438) / 3600}
idle["vcpu_h"] = 3 * 6 * idle["hours"]
idle["usd_modeled"] = idle["vcpu_h"] * RATE_VCPU_H

# Per-operation economics from phases 1-3
p1 = json.loads((OUT / "p1_summary.json").read_text())
p2 = json.loads((OUT / "p2_summary.json").read_text())
p3 = json.loads((OUT / "p3_summary.json").read_text())

# Phase 2 break-even: queries needed for Trino CPU saved to repay compaction CPU
tmq = [r for r in tm if r["kind"] == "query" and r.get("scored") and r.get("ok") and str(r.get("schema", "")).startswith(("v2p2_r2", "v2p2_r3", "v2p2_r4")) and "after" in r.get("phase", "")]
cpu = defaultdict(list)
for r in tmq:
    cpu[(r["table"], r["query"])].append(r["cpu_ms"])
cpu = {k: statistics.median(v) for k, v in cpu.items()}
breakeven = []
for arm in ("c256", "c128", "c512", "d256"):
    comp_vcpu_s = p2["maintenance"][arm]["vcpu_h"]["mean"] * 3600
    for qq in sorted({k[1] for k in cpu}):
        saved_ms = cpu[("a0", qq)] - cpu[(arm, qq)]
        breakeven.append({"arm": arm, "query": qq, "a0_cpu_ms": cpu[("a0", qq)], "arm_cpu_ms": cpu[(arm, qq)],
                          "cpu_saved_ms_per_query": saved_ms, "compaction_vcpu_s": comp_vcpu_s,
                          "breakeven_queries": comp_vcpu_s * 1000 / saved_ms if saved_ms > 0 else None,
                          "note": "query gets more expensive after compaction" if saved_ms <= 0 else ""})
write_csv("p5_breakeven.csv", breakeven)

cp_vcpu_deployed = 4.15
cp_vcpu_chart_default = 2.15  # 2 workers
month_h = 730
summary = {
    "label": "MODELED (requests x lifetime x published-rate assumptions). Not billed.",
    "rates": {"vcpu_h": RATE_VCPU_H, "s3_gb_month": RATE_S3_GB_MONTH, "s3_put_1k": RATE_S3_PUT_1K,
              "s3_get_1k": RATE_S3_GET_1K, "nat_gb_worst_case": RATE_NAT_GB},
    "v2_ledger": {"compute_by_category": dict(ledger), "compute_usd": compute_usd,
                  "s3_bytes_now": s3["bytes"], "s3_objects_now": s3["objects"], "v2_days": v2_days,
                  "storage_usd_upper": storage_usd, "trino_s3_requests_measured": trino_reqs,
                  "requests_usd_modeled": requests_usd, "nat_gb_upper": nat_gb, "nat_usd_worst_case": nat_usd_worst,
                  "v2_total_usd_excl_nat": compute_usd + storage_usd + requests_usd,
                  "v2_total_usd_incl_nat_worst": compute_usd + storage_usd + requests_usd + nat_usd_worst,
                  "prior_v0_v1_usd_upper": 160.0,
                  "idle_trino_waste": idle},
    "control_plane_fixed_monthly_usd": {"as_deployed_6_workers": cp_vcpu_deployed * month_h * RATE_VCPU_H,
                                        "chart_default_2_workers": cp_vcpu_chart_default * month_h * RATE_VCPU_H,
                                        "scanner_cronjob_4x0.5vcpu_per_6h_runs": "included in measured ledger"},
    "maintenance_cost_per_cycle_usd": {
        "p1_10_small_tables": {arm: p1["per_arm"][arm]["vcpu_h_per_trial"]["mean"] / 4 * RATE_VCPU_H for arm in ("a1", "a2", "a3")},
        "p1_a3_excluding_expire": statistics.fmean(p1["headline"]["excluding_expire"]["a3_vcpu_h"]) / 4 * RATE_VCPU_H,
        "p2_100GiB_one_compaction": {arm: p2["maintenance"][arm]["vcpu_h"]["mean"] * RATE_VCPU_H for arm in ("c256", "c128", "c512", "d256")},
        "p3_2h_cdc": {"a1": p3["a1"]["vcpu_h"] * RATE_VCPU_H, "a3": p3["a3"]["vcpu_h"] * RATE_VCPU_H},
    },
    "total_cost_saved_pct": "NOT_ESTABLISHED: Cryo used more maintenance compute than A1 in phases 1-3 and adds a fixed control plane; "
                            "the engineering cost of building and running custom Spark/Airflow maintenance was not measured.",
}
# Break-even engineering time: Cryo pays off when avoided engineering cost per month exceeds its extra cost
summary["break_even_engineering_hours_per_month"] = {
    "formula": "(control_plane_monthly + extra_maintenance_compute_monthly) / engineer_hourly_cost",
    "control_plane_monthly_as_deployed": summary["control_plane_fixed_monthly_usd"]["as_deployed_6_workers"],
    "control_plane_monthly_chart_default": summary["control_plane_fixed_monthly_usd"]["chart_default_2_workers"],
    "note": "Engineer hourly cost and actual DIY maintenance effort are organisation inputs; not measured here.",
}
write_json("p5_cost_summary.json", summary)
print(json.dumps(summary, indent=1, default=lambda x: round(x, 4) if isinstance(x, float) else str(x)))
for b in breakeven:
    print(b["arm"], b["query"], b["a0_cpu_ms"], b["arm_cpu_ms"], b["breakeven_queries"] and round(b["breakeven_queries"]))
