# Cryo UI/UX Comparative Review — Seed

**Created:** 2026-07-29
**Status:** Seed template — **no scores recorded yet**
**Owner:** TBD
**Next re-certification due:** 2026-10-27 (90-day cadence per Part 12.1.4)

> This file is the **only** record that satisfies Part 9.2 / Part 12.4 dimension 10
> ("Competitive parity") of the Cryo Frontend Revamp plan. Per Part 12.1.1, the
> 10/10 claim is **not** scoreable by the team that built the redesign.
>
> Do not enter self-graded scores here. The whole point of this document is to
> capture external, blind, paired reviews and their justifications, so the rating
> claim is falsifiable rather than self-marketing.
>
> Each completed review renames this file to `UIUX_COMPARATIVE_REVIEW_<YYYY-MM-DD>.md`
> and updates the status below.

---

## Reviewer roster (Part 12.1.1 requirements)

For each reference product, the reviewer must have **real, recent, hands-on
production experience** with that product — not "have used it before." Internal
team members who helped build the Cryo redesign **do not qualify.**

| Reference product | Reviewer name | Background / recency | Date of review | Score recorded |
|---|---|---|---|---|
| **Airflow 3** (Operations parity) | _TBD_ | Has actively run Airflow in production within the last 6 months | — | — |
| **OpenMetadata** (Table detail parity) | _TBD_ | Has administered OpenMetadata in production within the last 6 months | — | — |
| **Linear** (Shell polish parity) | _TBD_ | Daily Linear user for ≥3 months | — | — |

If a reviewer cannot be recruited within 30 days of this template being created,
the 10/10 claim is **withheld** until one is — Part 12.9 explicitly disqualifies
self-graded category leadership as an instant fail.

---

## Method (Part 12.1.1 verbatim)

1. **Blind, paired screenshots/recordings.** Reviewer does not know which
   screenshot is Cryo and which is the reference product going in. Order is
   randomized; identifiers stripped.
2. **Per-dimension scoring 1–10** against the Part 12.4 rubric, with a required
   one-line justification per score (a bare number without reasoning doesn't
   count).
3. **Ship threshold:** median reviewer score ≥ 8/10 per dimension, with no
   reviewer scoring below 6 on any dimension (a single very negative outlier is
   a real signal, not noise to average away).

---

## Score grid (per Part 12.4 dimensions × reference product)

Reviewers fill in one row per dimension per product. Justification column is
**required**; bare scores are discarded.

| # | Dimension | Airflow 3 score | Justification | OpenMetadata score | Justification | Linear score | Justification |
|---|---|---|---|---|---|---|---|
| 1 | Visual coherence | — | — | — | — | — | — |
| 2 | Information architecture | — | — | — | — | — | — |
| 3 | Operator workflows | — | — | — | — | — | — |
| 4 | Data visualization | — | — | — | — | — | — |
| 5 | Accessibility | — | — | — | — | — | — |
| 6 | Motion & interaction | — | — | — | — | — | — |
| 7 | Performance | — | — | — | — | — | — |
| 8 | Empty / error / loading | — | — | — | — | — | — |
| 9 | Discoverability | — | — | — | — | — | — |
| 10 | Category-specific (product surface) | — | — | — | — | — | — |

**Ship threshold per product:**
- Median ≥ 8/10 across all dimensions
- No single reviewer dimension < 6

---

## Qualitative friction log

Beyond the scored grid, capture what reviewers reached for in the reference
product that wasn't there in Cryo (and vice versa). These notes are how
"feels-premium" gaps surface that don't fit the 10-dimension grid.

### Airflow 3 review

_Free-form notes from reviewer session. Pending first review._

### OpenMetadata review

_Free-form notes from reviewer session. Pending first review._

### Linear review

_Free-form notes from reviewer session. Pending first review._

---

## Connection to other Part 12 evidence

| Mechanism | Status | Evidence file |
|---|---|---|
| 12.1.1 blind external comparison | **Pending reviewer recruitment** | _this file_ |
| 12.1.2 real-scale perf (13,300+ tables) | Pending — synthetic fleet not yet run | TBD |
| 12.1.3 2-week operator soak + SUS ≥80 | Pending — soak not yet started | TBD |
| 12.1.4 90-day re-certification | _This file_ is the artefact | _this file_ |

The 10/10 claim is **only valid** when all four rows above are green and a
current `UIUX_COMPARATIVE_REVIEW_<date>.md` is checked in.

---

## Status trail

| Date | Status | Filed by | Notes |
|---|---|---|---|
| 2026-07-29 | Seed created | plan executor | Template established; reviewers not yet recruited |
| _TBD_ | First review completed | _reviewer_ | Rename file to `UIUX_COMPARATIVE_REVIEW_<date>.md` |