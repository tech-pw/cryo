"""Charts for the V2 results, drawn only from results/v2/analysis outputs."""
import csv, json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from common import OUT

CH = OUT / "charts"
CH.mkdir(exist_ok=True)
p1 = json.loads((OUT / "p1_summary.json").read_text())
p2 = json.loads((OUT / "p2_summary.json").read_text())

# 1. Phase 1: operations, unnecessary operations, vCPU-h per trial by arm
arms = ["a1", "a2", "a3"]
labels = ["A1 naive DAG", "A2 candidate", "A3 Cryo"]
fig, ax = plt.subplots(1, 3, figsize=(13, 4))
for i, (key, title) in enumerate([("ops_per_trial", "Operations per trial"),
                                  ("unnecessary_ops_per_trial", "Unnecessary operations per trial"),
                                  ("vcpu_h_per_trial", "Allocated vCPU-hours per trial")]):
    vals = [p1["per_arm"][a][key]["mean"] for a in arms]
    errs = [p1["per_arm"][a][key]["ci95"] or 0 for a in arms]
    ax[i].bar(labels, vals, yerr=errs, color=["#888", "#4a7", "#36c"], capsize=4)
    ax[i].set_title(title)
    ax[i].tick_params(axis="x", labelsize=8)
fig.suptitle("Phase 1: 10 tables x 4 cycles, trials 7-9 (mean, 95% CI)")
fig.tight_layout()
fig.savefig(CH / "p1_ops_compute.png", dpi=120)

# 2. Phase 1: query latency ratio vs A0 by class
rows = list(csv.DictReader((OUT / "p1_query_ratios.csv").open()))
classes = ["healthy", "moderate", "severe", "updates", "lowtraffic"]
fig, ax = plt.subplots(1, 3, figsize=(14, 4), sharey=True)
for i, q in enumerate(["q_agg", "q_filter", "q_point"]):
    for j, a in enumerate(arms):
        ys = [float(next(r["ratio_geomean"] for r in rows if r["class"] == c and r["query"] == q and r["arm"] == a and r["vs"] == "a0"))
              for c in classes]
        ax[i].bar([k + j * 0.27 for k in range(len(classes))], ys, width=0.27, label=labels[j])
    ax[i].axhline(1, color="k", lw=0.8)
    ax[i].set_xticks([k + 0.27 for k in range(len(classes))], classes, fontsize=8)
    ax[i].set_yscale("log")
    ax[i].set_title(f"{q}: latency / A0 (lower is faster)")
ax[0].legend(fontsize=8)
fig.tight_layout()
fig.savefig(CH / "p1_query_ratio.png", dpi=120)

# 3. Phase 2: speedup vs a0 per query
rows = [r for r in csv.DictReader((OUT / "p2_query_speedup.csv").open()) if r.get("speedup_vs_a0")]
qs = sorted({r["query"] for r in rows})
p2arms = ["c256", "c128", "c512", "d256"]
fig, ax = plt.subplots(figsize=(12, 4))
for j, a in enumerate(p2arms):
    ys = [float(next(r["speedup_vs_a0"] for r in rows if r["query"] == q and r["arm"] == a)) for q in qs]
    ax.bar([k + j * 0.2 for k in range(len(qs))], ys, width=0.2, label=a)
ax.axhline(1, color="k", lw=0.8)
ax.set_xticks([k + 0.3 for k in range(len(qs))], qs, fontsize=8)
ax.set_ylabel("speedup vs untouched (x)")
ax.set_title("Phase 2: 100 GiB table, trials 2-4 (geometric mean)")
ax.legend()
fig.tight_layout()
fig.savefig(CH / "p2_speedup.png", dpi=120)

# 4. Phase 2: compaction cost
fig, ax = plt.subplots(1, 2, figsize=(10, 4))
for i, (k, t) in enumerate([("wall_s", "Wall time (s)"), ("vcpu_h", "Allocated vCPU-hours")]):
    ax[i].bar(p2arms, [p2["maintenance"][a][k]["mean"] for a in p2arms],
              yerr=[p2["maintenance"][a][k]["ci95"] or 0 for a in p2arms], capsize=4, color=["#36c", "#69c", "#9bd", "#4a7"])
    ax[i].set_title(t)
fig.suptitle("Phase 2: one compaction of 40 small-file partitions (67 GiB rewritten)")
fig.tight_layout()
fig.savefig(CH / "p2_cost.png", dpi=120)

# 5. Phase 3: delete files over time
rows = list(csv.DictReader((OUT / "p3_file_series.csv").open()))
fig, ax = plt.subplots(1, 2, figsize=(12, 4))
for i, metric in enumerate(["delete_files", "data_files"]):
    for a, lab in (("a0", "A0 none"), ("a1", "A1 naive, 30 min"), ("a3", "A3 Cryo, 10 min")):
        pts = sorted((float(r["t_rel_s"]) / 60, int(r[metric])) for r in rows if r["arm"] == a and r["table"] == "orders")
        ax[i].step([p[0] for p in pts], [p[1] for p in pts], where="post", label=lab)
    ax[i].set_xlabel("minutes since stream start")
    ax[i].set_title(f"orders (CDC MERGE): {metric}")
ax[0].legend()
fig.tight_layout()
fig.savefig(CH / "p3_orders_files.png", dpi=120)
print(sorted(p.name for p in CH.iterdir()))
