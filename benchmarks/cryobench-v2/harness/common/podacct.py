"""Pod accounting for the benchmark namespace.

Lists pods every POLL seconds and emits `PA {...}` when a pod is first seen, when its phase
changes, and when it disappears. Executors are deleted on termination, so this is the only
record of their lifetime and requested resources. Allocated CPU-hours are computed from
these records offline (requests x lifetime), not from utilization.
"""
import json, os, time
from kubernetes import client, config

NS = os.environ.get("NAMESPACE", "cryo-bench")
POLL = float(os.environ.get("POLL", "5"))
config.load_incluster_config()
v1 = client.CoreV1Api()


def qty(v):
    if v is None:
        return 0.0
    v = str(v)
    units = {"m": 1e-3, "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "K": 1e3, "M": 1e6, "G": 1e9}
    for u in ("Ki", "Mi", "Gi", "Ti", "m", "K", "M", "G"):
        if v.endswith(u):
            return float(v[: -len(u)]) * units[u]
    return float(v)


def summarize(p):
    req = {"cpu": 0.0, "memory": 0.0}
    lim = {"cpu": 0.0, "memory": 0.0}
    for c in p.spec.containers:
        r = c.resources
        for k in req:
            req[k] += qty((r.requests or {}).get(k)) if r else 0
            lim[k] += qty((r.limits or {}).get(k)) if r else 0
    starts, ends = [], []
    for cs in p.status.container_statuses or []:
        st = cs.state
        if st and st.running and st.running.started_at:
            starts.append(st.running.started_at.timestamp())
        if st and st.terminated:
            if st.terminated.started_at:
                starts.append(st.terminated.started_at.timestamp())
            if st.terminated.finished_at:
                ends.append(st.terminated.finished_at.timestamp())
    owner = (p.metadata.owner_references or [None])[0]
    return {
        "uid": p.metadata.uid, "name": p.metadata.name, "phase": p.status.phase,
        "node": p.spec.node_name, "labels": p.metadata.labels or {},
        "owner": f"{owner.kind}/{owner.name}" if owner else None,
        "created": p.metadata.creation_timestamp.timestamp() if p.metadata.creation_timestamp else None,
        "start": min(starts) if starts else None, "end": max(ends) if ends else None,
        "req_cpu": req["cpu"], "req_mem": req["memory"], "lim_cpu": lim["cpu"], "lim_mem": lim["memory"],
    }


def emit(event, rec):
    rec = dict(rec, event=event, seen=round(time.time(), 1))
    print("PA " + json.dumps(rec, default=str), flush=True)


known = {}
while True:
    try:
        pods = v1.list_namespaced_pod(NS).items
    except Exception as exc:
        print("PA_ERR " + json.dumps({"error": str(exc)[:200], "t": time.time()}), flush=True)
        time.sleep(POLL)
        continue
    now_uids = set()
    for p in pods:
        s = summarize(p)
        now_uids.add(s["uid"])
        prev = known.get(s["uid"])
        if prev is None:
            emit("seen", s)
        elif prev["phase"] != s["phase"] or (prev["end"] is None and s["end"] is not None):
            emit("update", s)
        known[s["uid"]] = s
    for uid in list(known):
        if uid not in now_uids:
            emit("gone", known.pop(uid))
    time.sleep(POLL)
