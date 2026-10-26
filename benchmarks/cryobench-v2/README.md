# CryoBench V2 — published harness and results

This is the CryoBench V2 harness and the result set the benchmark page on the Cryo
docs site is built from. Everything here is what was measured, with the internal
identifiers replaced by placeholders.

## What is here

```
CRYOBENCH_V2_PROTOCOL.md          the frozen protocol
PROTOCOL_FREEZE_SHA256SUMS.txt    the freeze, and the published checksums
CLAIMS.md                          the claims register: every claim, with its evidence
RESULTS.md                         the results write-up
COST_ANALYSIS.md                  the cost model

harness/                           the scripts that produced the results
  make_controller.py               builds a controller Job for a phase
  k8s-common.yaml                 the shared Kubernetes config
  common/                         load generators and measurement helpers
  p1/ p2/ p3/ p4/                 the phase controllers
  analysis/                       the scripts that turn raw logs into the tables below

results/v2/data/                  the parsed per-event JSONL
results/v2/analysis/              the analysis outputs, and the charts
```

## The phases

| Phase | What it measures |
|---|---|
| 1 | **The fleet decision.** Cryo's planner against a naive scheduled Spark DAG and against a threshold-gated expert baseline, on 10 tables × 4 compressed ingest cycles. Does Cryo do less unnecessary work, and at what cost? |
| 2 | **The 100 GiB table.** Query speed before and after compaction on one 100 GiB table with 7,234 small files, plus a check that Cryo's rendered SQL produces the same result as running it by hand. |
| 3 | **Continuous ingest.** Behaviour under MERGE ingestion every 200 s, where a naive DAG's compaction fails on every attempt. |
| 4 | **Discovery.** Whether Cryo finds every table a catalog registers, with correct file counts. |
| 6 | **Foreground impact.** Whether compaction changes live Trino query latency. |

`CLAIMS.md` is the register of what each phase does and does not establish. Read it
before quoting a number: several common claims are marked **NOT SUPPORTED** and
several are **NOT ESTABLISHED**, and those answers are the interesting part.

## Reproducing the analysis

The published data is enough to regenerate every analysis table:

```bash
cd benchmarks/cryobench-v2
for s in p1_analysis p2_analysis p3_analysis cost_analysis; do
  python harness/analysis/$s.py
done
```

Each script reads `results/v2/data/` (and, for the cost model,
`results/v2/raw/v2-s3usage.log`) and writes `results/v2/analysis/`. The outputs
are byte-identical to the ones committed here.

`make cryobench-v2-check` runs the first three and then diffs the result against
the committed output, so a drift in the published data fails rather than passing
quietly.

### The other scripts in `harness/analysis/`

Not all of them run without the cluster or credentials. They are published so
the pipeline is readable end to end, and each is labelled by what it needs:

| Script | What it does | Needs |
|---|---|---|
| `parse_logs.py` | Splits the raw controller and pod-accounting logs into the per-event JSONL in `results/v2/data/`. Takes the raw and output directories as arguments. | the raw logs, which are not published |
| `s3_usage.py` | Walks the object store and writes the per-prefix usage log. | AWS credentials |
| `snapshot_audit.py` | Audits the snapshot chain in the warehouse. | AWS credentials |
| `charts.py`, `site_charts.py` | Render the charts in `results/v2/analysis/charts/`. | the analysis output |

The raw controller logs are 392 MB and are not published — they are the input
to `parse_logs.py`, and the parsed data carries everything the analysis needs.

## Rerunning the benchmark

This needs a Kubernetes cluster and about a day. It is not something to run on a
laptop.

**Prerequisites**

- A Kubernetes cluster with enough capacity for the phase's pods. The controller
  Job's resource requests are in `harness/k8s-common.yaml`.
- An Iceberg REST catalog reachable from the cluster, backed by an object store
  the benchmark may write to. The scripts assert the warehouse prefix, so point
  them at a dedicated prefix — the credentials are broader than the prefix, so
  this is application-level protection, not IAM isolation.
- Spark and Trino images, matching the versions in `CRYOBENCH_V2_PROTOCOL.md`.
- `kubectl` and `aws` on the machine running the controller.

**Running one phase**

```bash
cd benchmarks/cryobench-v2
python harness/make_controller.py --phase p1 --trial 7 --cycle 1
kubectl apply -f harness/p1/controller-job.tmpl.yaml
```

Each phase's controller takes `--trial` and `--cycle`. The protocol fixes which
trials and cycles are valid; do not substitute your own — the statistics in
`CLAIMS.md` are computed over the registered set.

**What the phases cost.** Phase 1 and phase 2 each run several trials of
several minutes. The full protocol is the sum of those. Budget a cluster and a
day, and read the protocol's own notes on what the runs assume.

## The freeze

`PROTOCOL_FREEZE_SHA256SUMS.txt` records the SHA-256 of the protocol and the
controller scripts, so a reader can tell that the harness that produced the
results is the harness that is published here.

**The published files are not byte-identical to the ones that ran.** The internal
identifiers — the cluster name, the object-store bucket, the namespace, the
region and the node hostnames — were replaced with placeholders before
publishing, and that changed the bytes of the files the freeze covers.

So the file has two blocks:

- **the original freeze** — the checksums of the files as they ran, from the
  internal repository. These are the ones that matter for provenance: they
  attest that the protocol did not change while the data was collected.
- **the published checksums** — the checksums of the files here, after the
  identifier scrub.

Both are in the file, with the replacements listed. A reader who wants to verify
the published files matches the second block. A reader who wants to verify
provenance against the internal repository matches the first.

## The identifiers that were replaced

| Placeholder | What it was |
|---|---|
| `<benchmark-bucket>` | the object-store bucket the benchmark wrote to |
| `<benchmark-cluster>` | the EKS cluster the benchmark ran on |
| `cryo-bench` | the Kubernetes namespace the benchmark ran in |
| `<node>` | the EKS node hostnames, which carried the nodes' private IPs |
| `<home>` | the local home directory, in recorded paths |
| `<internal-host>`, `<internal-domain>` | internal infrastructure hostnames |

The AWS region (`ap-south-1`) is **not** replaced. It is public — the benchmark
page states it, it appears in the Kubernetes topology labels in the data, and it
is where S3 prices are published. Only the private IPs were sensitive.

The measured numbers were not changed. The analysis outputs regenerate
byte-identically from the scrubbed data, which is what proves it: a replacement
that altered a number would change an output.

## The hardware and versions

The cluster the numbers were measured on:

| | |
|---|---|
| Nodes | 6 (counted from the pod-accounting data: six distinct node hostnames) |
| Node type | `r6a.8xlarge` — 32 vCPU, 128 GiB — on-demand |
| Region | `ap-south-1` (Mumbai) |
| Spark | 4.1.2 |
| Iceberg | 1.11.0 (the catalog fixture is Iceberg 1.9.2) |
| Trino | 480 |
| Object store | S3 Standard |

The Trino pods are capped at 6 vCPU / 20 GiB guaranteed QoS. The Spark arms run in
local mode, and the protocol's resource budget is about 48 vCPU on average for
about 24 hours across the whole benchmark.

The per-phase pod shapes and requests are in `harness/k8s-common.yaml` and each
phase controller. The measured allocation is in `results/v2/data/pa.jsonl`.
