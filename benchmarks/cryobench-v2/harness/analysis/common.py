"""Shared loaders and statistics for the V2 analysis scripts."""
import json, math, pathlib, statistics
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT / "results" / "v2" / "data"
OUT = ROOT / "results" / "v2" / "analysis"
OUT.mkdir(parents=True, exist_ok=True)

T_CRIT = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571, 7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262,
          11: 2.228, 12: 2.201, 13: 2.179, 14: 2.160, 15: 2.145, 16: 2.131}


def load(fam):
    p = DATA / f"{fam}.jsonl"
    return [json.loads(l) for l in p.open()] if p.exists() else []


def mean_ci(xs):
    """Mean and 95% t-interval half-width across trials. None if fewer than 2 values."""
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0, "mean": None, "ci95": None}
    m = statistics.fmean(xs)
    if len(xs) < 2:
        return {"n": len(xs), "mean": m, "ci95": None}
    return {"n": len(xs), "mean": m, "ci95": T_CRIT[len(xs)] * statistics.stdev(xs) / math.sqrt(len(xs))}


def geo_ci(xs):
    """Geometric mean and 95% t-interval (on logs) across trials, returned as [lo, hi]."""
    xs = [x for x in xs if x is not None and x > 0]
    if not xs:
        return {"n": 0, "geomean": None, "lo": None, "hi": None}
    ls = [math.log(x) for x in xs]
    m = statistics.fmean(ls)
    if len(xs) < 2:
        return {"n": len(xs), "geomean": math.exp(m), "lo": None, "hi": None}
    h = T_CRIT[len(xs)] * statistics.stdev(ls) / math.sqrt(len(xs))
    return {"n": len(xs), "geomean": math.exp(m), "lo": math.exp(m - h), "hi": math.exp(m + h)}


def pct(xs, q):
    """Nearest-rank percentile; None when the sample cannot support it (n < 1/(1-q))."""
    xs = sorted(xs)
    if not xs or len(xs) < 1 / (1 - q):
        return None
    k = max(0, math.ceil(q * len(xs)) - 1)
    return xs[k]


def pods():
    """Last-known record per pod uid from the pod accountant, with a usable [start, end] interval."""
    last = {}
    for r in load("pa"):
        prev = last.get(r["uid"])
        if prev is None or r["seen"] >= prev["seen"]:
            if prev is not None:
                r = {**prev, **{k: v for k, v in r.items() if v is not None}}
            last[r["uid"]] = r
    capture = max((p["seen"] for p in last.values()), default=None)
    out = []
    for p in last.values():
        start = p.get("start") or p.get("created")
        end = p.get("end") or (p["seen"] if p.get("event") == "gone" else None)
        if end is None and p.get("phase") == "Running":
            end, p = capture, {**p, "still_running": True}
        if start is None or end is None or end < start:
            continue
        out.append({**p, "t0": start, "t1": end, "vcpu_h": p["req_cpu"] * (end - start) / 3600,
                    "gib_h": p["req_mem"] / 2**30 * (end - start) / 3600})
    return out


def cryo_spark_pods(all_pods, dest_id):
    """Every pod Cryo dispatched to a destination: submitter Job pods (labelled), their Spark
    drivers (named <job>-driver), and the executors those drivers own."""
    jobs = {p["labels"].get("job-name") for p in all_pods
            if p["labels"].get("cryo/destination") == dest_id and p["labels"].get("job-name")}
    drivers = {f"{j}-driver" for j in jobs}
    sel = []
    for p in all_pods:
        if p["labels"].get("job-name") in jobs or p["name"] in drivers or \
                (p.get("owner") or "").removeprefix("Pod/") in drivers:
            job = p["labels"].get("job-name") or p["name"].removesuffix("-driver")
            if (p.get("owner") or "").startswith("Pod/"):
                job = p["owner"].removeprefix("Pod/").removesuffix("-driver")
            sel.append({**p, "k8s_job": job})
    return sel


def write_json(name, obj):
    (OUT / name).write_text(json.dumps(obj, indent=1, default=str))


def write_csv(name, rows):
    if not rows:
        (OUT / name).write_text("")
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with (OUT / name).open("w") as f:
        f.write(",".join(keys) + "\n")
        for r in rows:
            f.write(",".join("" if r.get(k) is None else str(r.get(k)).replace(",", ";") for k in keys) + "\n")
