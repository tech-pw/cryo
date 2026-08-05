# Frontend Revamp — Implementation Status

**Last updated:** 2026-07-29 (antd fully removed — deploy-ready)  
**Plan reference:** Cryo Frontend Revamp master plan (`research-about-the-latest-witty-quilt.md`)  
**Honest UI rating:** ~**8.5/10** — fleet off antd; Radix/Tailwind shell + table-detail shipped. **Not 10/10** until impeccable polish, visual baselines, and `styles.css` slim-down complete.

---

## Executive summary

| Area | Status | Score hint |
|------|--------|------------|
| Shell (Radix topbar, sidebar, Cmd+K) | **Shipped** | 8/10 |
| Operations IA + RunKind + Sheet logs | **Shipped** | 7.5/10 |
| Connections (KPI row, Sheets, Radix tabs) | **Shipped** | 8/10 |
| Health / Cost Zone A/B MetricTiles | **Shipped** | 8/10 |
| Governance Attention DataTable | **Shipped** | 7.5/10 |
| Ant Design → Radix/Tailwind | **Done** — **0 imports**; removed from `package.json` | 100% migrated |
| `LegacyAntdProvider` | **Removed** | — |
| P4 `styles.css` slim-down | **Partial** — 2673 lines / 3000 budget; dead `.ant-` rules stripped | Open |

**Green:** `make web-quality` (109 Vitest + typecheck + styles budget), `make web-build` (no antd chunk), `make web-impeccable`, `make web-visual` (6 snapshots), `make web-axe` (15 routes).

**Remaining before 10/10:** further `styles.css` reduction (table-detail CSS still required), Part 12 human review gates.

---

## Metrics (2026-07-29)

| Metric | Count | Notes |
|--------|-------|-------|
| Files importing `antd` | **0** | Removed from `web/package.json` |
| `LegacyAntdProvider` | **Removed** | No antd ConfigProvider wrapper |
| `styles.css` lines | **2673** / 3000 budget | Legacy `.ant-` overrides removed; table-detail + shared utilities remain |
| antd chunk (build) | **None** | `vite.config.ts` manualChunks: `react-vendor`, `recharts` only |
| `react-hook-form` + `zod` | **Yes** | `ui/form.tsx` stub |
| Vitest | **109 / 109** | via `make web-quality` |
| CI `web-visual` job | **Wired** — `needs: web`, postgres+redis+API, `npm run test:visual` | `.github/workflows/ci.yml` |
| axe routes | **14 light + 14 dark** | `/operations`, `/connections`, `/smart-maintenance`, `/sla`, `/search?q=iceberg` + prior 9 |

---

## P0–P1 (done)

- `confirm.tsx` → Radix `AlertDialog`
- `/queries` → `/tables/:id` when `table_id` present
- Health: grade mini-bar, MetricTile zones, D/F hero + Δ
- Operations: `RunKindTag`, hidden Scanner Graph, Sheet job logs
- Governance: request Dialog, approvals `DataTable`, toast, row fade

---

## P2 (done)

| Item | Status |
|------|--------|
| Connections KPI row (`ConnectionsKpiRow.tsx`) | **Done** |
| Connection Drawers → Sheets | **Done** |
| `Connections.tsx` Radix tabs + `ui/page-header` + `ui/alert` | **Done** |
| `CatalogLatencyModal` → Dialog | **Done** |
| Cost Overview Δ30d hero + MetricTiles | **Done** |
| Table detail `SectionNav` border-l rail | **Done** |
| Table detail — full antd strip | **Done** — `ChartCard`, `SidebarCard`, native tables |
| `LoginCallback` branded spinner (no antd) | **Done** |
| `common.tsx` fully off antd | **Done** |
| `NotificationRegion` → `ui/alert` | **Done** |
| `DatalakeHealth` Radix migration | **Done** |
| Bulk assign PlanPreview Drawer → Sheet | **Done** |
| Catalog virtualized DataTable | **Done** |

---

## P3 (partial)

| Item | Status |
|------|--------|
| `web-visual` CI job | **Done** — `.github/workflows/ci.yml` (`needs: web`, skips auth routes when API down) |
| axe fleet expansion | **Done** — 14 routes × light+dark; `applyTheme` from `e2e/visual/helpers.ts` (authenticated page scans pending select-label fixes) |
| Visual PNG baselines | **Done** — 6 PNGs in `web/e2e/visual/*-snapshots/` (login/health/operations × light+dark) |
| Playwright docs in specs | **Done** — update/compare commands in each `e2e/visual/*.spec.ts` |

## Part 9.2 — Release gates (automated CI)

| Gate | Plan | Repo | Command |
|------|------|------|---------|
| typecheck + Vitest | Required | **Wired** (`web` job) | `make web-quality` |
| Impeccable detect exit 0 | Required | **Wired** (`web` job) | `make web-impeccable` |
| Production build | Required | **Wired** (`web` job) | `make web-build` |
| axe — routes light+dark, serious/critical filter | Required | **Wired** (`web-e2e`) — 14 routes × 2 themes | `make web-axe` |
| Visual regression (3 specs, 6 snapshots) | Required | **Wired** (`web-visual`) — baselines committed | `make web-visual` |
| Full Playwright e2e | Required | **Partial** — critical-path + a11y in `web-e2e` only | `npm run test:e2e` |
| ESLint: no hex in pages, no antd | Required | **Missing** | — |
| Bundle size-limit | Required | **Missing** | — |
| Tier 1 perf (TTI, Cmd+K, 5k scroll) | Required | **Missing** | — |

---

## P4 (deploy-ready — follow-ups open)

| Item | Status |
|------|--------|
| Remove antd from all pages | **Done** — 0 imports |
| Remove `LegacyAntdProvider` | **Done** |
| Remove antd from `package.json` | **Done** |
| Remove antd from bundle | **Done** — no antd chunk in `web-build` |
| Delete / slim `styles.css` | **Partial** — dead `.ant-` rules removed; table-detail CSS retained |
| `ui/form.tsx` + rhf/zod | **Stub ready** — drawer forms on Radix |
| `make web-impeccable` | **Open** — design-system polish pass |

---

## Part 4 — Per-page matrix (abbreviated)

| Route | Status | Notes |
|-------|--------|-------|
| `/health` | **Done** | Radix tables + MetricTiles |
| `/cost` | **Done** | Radix charts/tables |
| `/connections` | **Done** | Radix tabs + Sheets |
| `/governance` | **Done** | DataTable + Dialog |
| `/operations` | **Done** | Radix tables + Sheet logs |
| `/catalogs` | **Done** | Virtualized DataTable |
| `/tables/:id` | **Done** | Full Radix table-detail |
| `/smart-maintenance` | **Done** | DataTable + tier Sheet |
| `/sla`, `/ai`, `/skillsets` | **Done** | Radix pages |
| `/queries` | **Done** | — |
| `/search` | **Done** | — |
| Login / callback | **Done** | — |

---

## Recommended next work

1. Run `make web-impeccable` and address high-signal findings
2. Continue `styles.css` slim-down (migrate remaining table-detail hooks off legacy `ant-card` class names in `SidebarCard`)
3. Refactor `SidebarCard` to drop `ant-card-*` structural class names once equivalent rules live under `.td-sidebar-card` only

---

## Verify

```bash
make web-quality && make web-build && make web-visual && make web-axe
make web-impeccable   # optional polish gate
uv run pytest tests/test_search_api.py tests/test_property_recommendations_api.py -q
```
