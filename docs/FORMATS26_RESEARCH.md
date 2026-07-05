# FORMATS '26 — Research Triage (2026-07-05)

Workshop: [1st International Workshop on Data FORMATS](https://dataformats.org/) (June 2026).  
Accepted papers: [dataformats.org/accepted-papers.html](https://dataformats.org/accepted-papers.html).

This doc records what we picked, what we deferred, PDF availability, and how it maps to Cryo.

---

## Decision summary

| Paper | PDF found? | Cryo action |
|-------|------------|-------------|
| **Zero-Scan DQ** (Verma et al., LinkedIn) | Yes — [arXiv:2605.30308](https://arxiv.org/pdf/2605.30308) | **Already shipped** (`cryo/scanner/quality.py`, profiler, Puffin NDV stub) |
| **Li et al. — How Consistent and Fresh are Lake Tables Really** | **No** — ACM DL only; listed on [Google Scholar (Bian)](https://scholar.google.com/citations?user=pCcoxYUAAAAJ) but no preprint | **Implemented metadata freshness** (`last_snapshot_committed_at` from Iceberg snapshots; SLA uses commit time not `scan_at`) |
| **Commutative Compaction** (Douglas/Hellerstein) | ACM DL only | **Watch list** — no sprint until a concrete Iceberg/Trino integration path exists |
| **Metadata-as-Data** (Hudi, Sumit et al.) | ACM DL only | **Do not build** — Hudi-specific write-path index substrate; off-mission for Cryo |
| **Puffin vector indexes** (Borycki et al., if cited) | N/A at FORMATS | **Pattern only** — sidecar binding for sketches; no ANN index work |
| **LakeVilla / TreeCat** | arXiv for LakeVilla | **Excluded** — transaction tooling, not maintenance observability |

---

## What we implemented (without waiting for Li PDF)

Li et al. almost certainly measures **catalog/metadata-visible** consistency and freshness (snapshot commit timestamps, isolation semantics) — the same class of signals Iceberg already stores at write time. Cryo had a semantic bug: SLA “freshness” used **scanner `scan_at`**, not **last snapshot commit**.

**Fix (2026-07-05):**

- `cryo/scanner/analyze.py` — `_snapshot_info()` now derives `first_snapshot_at` and `last_snapshot_committed_at` from all snapshots (min/max commit time).
- `cryo/scanner/health.py` — snapshot-age deduction uses `last_snapshot_committed_at` (staleness), not table creation time.
- `cryo/api/dashboard_routes.py` — `/api/sla-status` freshness = hours since last commit; adds `scan_staleness_hours` for Cryo scan lag.

When the Li paper becomes public, compare their SLO definitions and any cross-engine consistency metrics; extend only if they define checks we do not already cover (e.g. read-your-writes lag under concurrent writers).

---

## PDF search notes

| Paper | Sources tried | Result |
|-------|---------------|--------|
| Zero-Scan DQ | arXiv, HTML, PDF mirror | Full text at [arxiv.org/pdf/2605.30308](https://arxiv.org/pdf/2605.30308) |
| Li et al. freshness | arXiv, author pages, Scholar, ACM DL | **Paywalled / not posted** — contact Haoqiong Bian (Renmin U.) for preprint |
| Commutative Compaction | arXiv, workshop program | ACM DL link only — likely vision/workshop short paper |
| Hudi Metadata-as-Data | arXiv | Not on arXiv; ACM DL only |

---

## Explicitly deferred AI tracks (still correct)

These are **by design** in `docs/ARCHITECTURE_AND_AI_STRATEGY.md` §2.6–2.7 and `docs/EXECUTION_PLAN.md` Phase 9 — not backlog neglect.

### Full RL scheduling (Decima-style)

- **Why defer:** Sim-to-real transfer for RL schedulers remains unsolved; policies are hard to audit when they gate compaction on production tables.
- **What we ship instead:** Contextual bandit (`cryo/governor/bandit.py`) — narrower action space, degrades to heuristics under uncertainty.

### Chronos / TimesFM / Moirai (TS foundation models)

- **Why defer:** 2025 observability benchmarks show these models degrade on Cryo’s exact data shape: sparse, per-table, low-frequency fleet metrics across thousands of entities — not dense retail/energy series.
- **What we ship instead:** Rule + history-based anomaly detection and TabPFN-style **static** health classification from current stats.

### TabPFN-TS (tabular forecasting with metadata covariates)

- **Why defer:** Best *conceptual* fit (fleet metadata + per-table history), but NeurIPS-workshop stage, unproven outside GIFT-Eval-style benchmarks, no Iceberg tooling precedent.
- **Status:** Dedicated research spike only — not a committed feature until a validated offline eval on Cryo’s `table_stats_history` exists.

### GNN join / lineage discovery (OmniMatch et al.)

- **Why defer:** Real research, but needs fleet-scale column sampling, negative mining, and model serving — a separate product surface, not maintenance orchestration.
- **What we ship instead:** Embedding similarity over table/column names (`cryo/ai/similarity.py`) as a cheap, auditable precursor.

### Causal Sawmill (realized savings rigor)

- **Why defer:** Do-calculus / diff-in-diff needs **outcome history** (`actual_bytes_freed`, latency before/after maintenance with controls). Cryo’s learned cost model only recently started accumulating labels.
- **When to revisit:** After ≥90 days of paired maintenance outcomes + query latency per table for synthetic control or diff-in-diff design.

---

## Next steps (optional)

1. Email Li et al. for preprint — validate whether they define SLOs beyond snapshot commit time.
2. Puffin Theta/KLL sketches in sidecars — follow Zero-Scan §4; stub exists, full merge path is Phase 10+ if DQ coverage gaps justify it.
3. TabPFN-TS spike — offline notebook on `table_stats_history` before any API surface.
