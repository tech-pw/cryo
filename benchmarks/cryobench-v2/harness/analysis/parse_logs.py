"""Split raw V2 logs into one JSONL table per event family.

Inputs: controller logs (CTRL lines plus `CHILD <job> <line>` copies of child output) and the
pod-accounting log (PA lines). Output: results/v2/data/<family>.jsonl, every record tagged
with its source file and, for child lines, the child job name.

Usage: python parse_logs.py RAW_DIR OUT_DIR
"""
import json, pathlib, sys
from collections import defaultdict

PREFIXES = {"CTRL": "ctrl", "P1": "p1", "P2": "p2", "P3": "p3", "TM": "tm", "FG": "fg", "FG_END": "fg_end",
            "FX": "fx", "FX_SUMMARY": "fx_summary", "PA": "pa", "CP": "cp", "SNAP": "snap", "SNAP_ERR": "snap_err"}


def records(path):
    for raw in path.read_text(errors="replace").splitlines():
        job = None
        line = raw
        if line.startswith("CHILD "):
            _, job, line = line.split(" ", 2)
        head, _, body = line.partition(" ")
        fam = PREFIXES.get(head)
        if fam is None or not body.startswith("{"):
            continue
        try:
            rec = json.loads(body)
        except json.JSONDecodeError:
            continue
        rec["_key"] = json.dumps(rec, sort_keys=True, default=str)
        rec["_src"] = path.name
        if job:
            rec["_job"] = job
        yield fam, rec


def main(raw_dir, out_dir):
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    buckets = defaultdict(list)
    seen = set()
    for p in sorted(pathlib.Path(raw_dir).glob("*.log")):
        for fam, rec in records(p):
            key = (fam, rec.pop("_key"))
            if key in seen:
                continue
            seen.add(key)
            buckets[fam].append(rec)
    for fam, recs in buckets.items():
        with open(out / f"{fam}.jsonl", "w") as f:
            for r in recs:
                f.write(json.dumps(r, default=str) + "\n")
        print(f"{fam}: {len(recs)}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
