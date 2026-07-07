# Cryo — Open-Source Readiness Plan

> Written 2026-07-07. Answers one question: **what has to be true before `shelf-project/Cryo`
> goes public, and in what order?** This is not a rehash of `PREMORTEM.md` /
> `PREMORTEM_ACTIONABLES.md` / `6_MONTH_PREMORTEM_ANALYSIS.md` — those cover production
> hardening. This doc covers the OSS-specific surface: licensing, repo hygiene, launch scope,
> positioning, and community process. Where an item overlaps a premortem finding, it's cited
> instead of duplicated.
>
> **Verdict up front:** the core (scan → plan → govern → execute → audit) is real and
> defensible against the competitive landscape in `COMPETITIVE_ANALYSIS_2026.md`. The blockers
> below are not "is the code good enough" — they're "does this repo tell the truth about
> itself yet." It doesn't, in at least one load-bearing place (§1.1). Fix that class of gap
> before anything else matters.
>
> **Implementation log:** §1.1, §1.3, §1.4, §2.2, §3 (most), §4, §5, §1.2 worked through
> in commits following this doc's authoring — see the §7 checklist for current state with
> `[DEFERRED]` items called out. Items still open: `helm install --dry-run` (needs a host
> with the `helm` CLI), org/repo settings (needs a `shelf-project` admin), `v0.1.0` tag
> (cut last, immediately before flipping visibility).

---

## 0. The thesis this plan follows

**Ship narrow, prove the core, hide or label the rest.** Every phase below exists to make that
true on the day the repo flips public — not to finish all 30+ features first. A GitHub visitor
who reads `README.md`, skims the nav, and pokes at two or three routes should find exactly what
was promised and nothing that silently fails. That's the whole bar.

---

## 1. P0 — Hard blockers (cannot go public without these)

### 1.1 License: pick one, add the file, fix the doc that already lies about it

**Finding:** `docs/GOVERNANCE.md:3` states *"Cryo is open source under the [Apache License
2.0](../LICENSE)"* and links to a `LICENSE` file. **That file does not exist.**
`README.md:270` separately says `## License` → `TBD`. Two docs in the same repo disagree, and
neither is backed by an actual file. Right now, legally, nobody can use, fork, or contribute to
this code — "all rights reserved" is the default in the absence of a license, regardless of
what any `.md` file claims.

This is the single most on-brand example of the exact failure mode `PREMORTEM.md` Scenario J
already names — docs asserting something the repo doesn't back up — so it's the first thing an
external reviewer with any OSS experience will notice.

**Action:**
- Confirm Apache-2.0 is actually the intended license (implied by `GOVERNANCE.md`, consistent
  with the deps Cryo already carries — PyIceberg, Trino client, FastAPI, etc. are all
  permissively licensed, so Apache-2.0 doesn't create a conflict).
- Add `LICENSE` at repo root with the real Apache-2.0 text.
- Add the standard `Copyright [year] [holder]` header convention Apache-2.0 recommends, or a
  `NOTICE` file if there's a specific attribution requirement for shelf-project.
- Update `README.md:270` from `TBD` to point at `LICENSE`.
- Add SPDX headers to source files if the maintainers want them (optional, common in Apache
  projects but not load-bearing for launch).

**Owner check needed:** whoever holds IP rights over this codebase (shelf-project) needs to
actually sign off on Apache-2.0 specifically — this plan assumes it because the docs already
assume it, but that assumption itself needs a real yes/no before the file goes in.

### 1.2 Confirm the premortem P0 fixes are still true at HEAD, not just as of 2026-07-05

`PREMORTEM_ACTIONABLES.md` claims all 🔴 P0 items are implemented (JWT signature verification,
AWS secret encryption, tenant isolation documented, Helm templates, native-engine fake-success
disabled, bandit key fix, PII fail-closed). I independently spot-checked three of these against
current code on 2026-07-06 and they held up (`cryo/security/jwt_tokens.py` does real
`jwt.decode(..., algorithms=[...])`; `cryo/security/crypto.py` does real Fernet
encrypt/decrypt on `secret_ref`). That's a good sign, but it was 3 of ~15 P0/P1 claims, checked
two days before this doc.

**Action:** before flipping the repo public, re-run the actual verification pass — not "does
the doc say fixed," but "grep the current code and confirm it." Specifically re-check, since
these are the ones a curious security-minded reader will check first:
- `cryo/engines/native/engine.py` — confirm `NativeEngine.execute()`'s OPTIMIZE path still
  can't fabricate `ok=True` success (per `PREMORTEM_ACTIONABLES.md` feature-level P0).
- `cryo/governor/bandit.py` — confirm the confidence-gate key-mismatch fix actually landed.
- `deploy/helm/cryo/templates/` — confirm real Deployment/Service/CronJob manifests exist, not
  just `Chart.yaml`/`values.yaml`, and that `helm install --dry-run` succeeds.
- `cryo/compliance/pii_gate.py` + `cryo/governor/governor.py` — confirm the PII bypass now
  requires a role check and fails closed on classifier error.

This is a 30-minute grep pass, not a re-audit — but it must happen the week of launch, not rely
on a two-day-old snapshot.

### 1.3 Repo hygiene files that don't exist yet

| File | Status | Why it matters for OSS specifically |
|---|---|---|
| `SECURITY.md` | Missing | Cryo runs destructive operations against production data and stores AWS credentials. A public repo with no vulnerability-disclosure process is itself a bad signal to the exact security-literate audience (data platform engineers) this product targets. |
| `CODE_OF_CONDUCT.md` | Missing (only referenced indirectly via `docs/GOVERNANCE.md`) | Standard expectation for any public repo taking outside PRs; costs nothing to add (CNCF or Contributor Covenant template). |
| `.github/ISSUE_TEMPLATE/` | Missing | Without templates, day-one issues will be low-signal ("doesn't work"). Two templates (bug report, feature request) is enough for launch. |
| `.github/PULL_REQUEST_TEMPLATE.md` | Missing | `CONTRIBUTING.md` already documents PR process in prose; a template enforces the parity-checklist mention from `agents.md` §5 gets acknowledged per-PR. |
| `CHANGELOG.md` | Missing | Given the repo is 5 days old with 28 migrations already, a changelog started *now* — even a one-line "Initial public release" entry — establishes the habit before history gets hard to reconstruct. |

**Action:** add all five before launch. None of these are large — this is an afternoon, not a
sprint.

### 1.4 Scrub anything that shouldn't be public forever

Once pushed, git history is effectively permanent (rewriting history on a public repo post-launch
is far more disruptive than checking now). Verify:
- No real API keys, tokens, or credentials anywhere in history — `docker-compose.yml`'s
  `POSTGRES_PASSWORD: cryo` / `CRYO_JWT_SECRET: dev-secret-change-in-prod` are fine (obviously
  dev-only, already gitleaks-scanned per `.github/workflows/ci.yml`), but confirm gitleaks has
  actually been run against full history (`gitleaks detect --log-opts="--all"` or equivalent),
  not just the diff of recent PRs.
- No customer names, internal Slack references, or non-public company data in test fixtures,
  seed scripts, or the `docs/chat-*-build-log.md`-style files (that filename itself looks like
  a raw session transcript accidentally left in `docs/` — check what's actually in it before
  launch; if it's an AI session log with internal context, it doesn't belong in a public repo).
- Confirm the `shelf-project/Cryo` GitHub org/repo name is the one actually intended for public
  launch (referenced consistently in `CONTRIBUTING.md`, `GOVERNANCE.md`) and that org-level
  settings (default branch protection, who has admin) are configured before the repo is flipped
  from private to public — not after.

---

## 2. P0/P1 — Scope narrowing for the v1 public surface

This is the highest-leverage thing in this whole plan and the one most likely to be skipped
under time pressure. `6_MONTH_PREMORTEM_ANALYSIS.md` already recommends *"target 10 features
done right... before expanding the surface again"* — this section makes that concrete for a
public launch specifically, where the audience is a stranger evaluating trust signals in one
sitting, not an internal team who already trusts the roadmap.

### 2.1 Launch-tier (keep visible, must be solid)

Scanner · health grading (A–F) · planner (partition-aware chunking) · governor (load-aware
Trino admission) · Trino engine · presets + assignment · bulk run · ad-hoc run/preview · audit
log · run console · auth (JWT/OIDC) · cost estimate. This is the `6_MONTH_PREMORTEM_ANALYSIS.md`
feature matrix's "Good" column, nearly verbatim.

### 2.2 Gate or clearly label "Experimental" (don't hide entirely — but don't present as done)

- **Native Rust engine** (`cryo-native`/`cryo-engine`) — per `PREMORTEM_ACTIONABLES.md`, neither
  builds in CI today. Pick one of the two trees, delete the other (maintaining two competing
  unfinished Rust engines in a public repo reads as unfocused, not ambitious), and label
  whichever survives "experimental, not production-ready" in the README and in-app, until it has
  a real CI build+test step.
- **Flink engine** — currently generates config only, doesn't submit to a cluster. Label as
  "config generation only" wherever it's exposed, don't imply execution.
- **ABAC** (`cryo/security/abac.py` / `abac_enforcement.py`) — confirm current wiring status
  (per `6_MONTH_PREMORTEM_ANALYSIS.md` C4 this was reportedly wired on maintenance-path in the
  2026-07 pass; re-verify per §1.2 above) and document exactly which paths it covers vs. doesn't.
- **MCP server** — same auth model as the API, can trigger real runs; document this loudly in
  its own section so nobody points an LLM agent at it assuming it's read-only.
- **Cost attribution** — already hidden from nav per existing fixes; keep it that way until the
  stub is real.

### 2.3 Spark/EMR engine

Marked "Partial" / "less battle-tested than Trino" internally. Doesn't need to be hidden — Trino
is clearly the flagship path — but the README and in-app copy should say so plainly rather than
presenting both engines as equally mature. This is a credibility question, not a code question.

### 2.4 The "advanced" AI/governance layer (copilot, lineage, FinOps forecast, tiering, anomaly
detection, GitOps policies)

Per the feature matrix these are mostly "Partial" and telemetry-dependent (empty without query
log ingestion). For launch:
- Either ship query-log ingestion **on by default** with a working out-of-box demo path (so
  these features have something to show), or
- Clearly mark them "requires telemetry ingestion, see docs" in-app so an evaluator doesn't
  conclude the feature is broken when it's actually just unconfigured.

Silence is the wrong choice either way — an empty dashboard with no explanation reads as a bug.

---

## 3. P1 — Trust & credibility signals

These are things a technically sophisticated evaluator (the actual target user) will check in
the first 15 minutes.

| Signal | Current state | Target before launch |
|---|---|---|
| Migration history | 28 revisions in 5 days (per `git log` / `migrations/versions/`) | Squash to a single baseline migration for new installs, per `PREMORTEM_ACTIONABLES.md` P2 item already on file. A 28-file migration history for a 5-day-old repo is a specific, visible tell that this was AI-velocity-built, which invites extra scrutiny on everything else. |
| `helm install` | Templates added per `6_MONTH...` N2, "ops runbook thin" | Actually run `helm install --dry-run` and, ideally, a real install against a kind/minikube cluster, before claiming "deployable to Kubernetes" in `README.md:1`. |
| Dependency pinning | `pyiceberg>=0.11.0,<0.11.99`, `boto3>=1.34.0,<2` — already has ceilings (verified in `pyproject.toml`, better than `PREMORTEM_ACTIONABLES.md`'s "open-ended" claim from 2026-07-05) | Good — just confirm this holds and add the canary CI job against next-minor pyiceberg per the existing P1 recommendation. |
| CI honesty | `ci.yml` runs gitleaks, ruff, mypy, pytest against real Postgres/Redis (per `PREMORTEM.md`'s own "what's not on this list" section — a genuine asset) | Keep as-is; this is one of the strongest existing credibility signals. Make sure the README's test-count claim (`~360 tests`) matches reality (`find tests -name "test_*.py"` currently shows 70 files / ~336 `def test_` — close enough, but re-verify at launch time, don't let it drift). |
| Docs-vs-code drift | Found twice in one review pass (LICENSE claim, plus the premortems' own documented history of this exact pattern) | Do one honest pass through every doc in `docs/`, `README.md`, `PRODUCT.md` before launch, checking each "done"/"fixed" claim against a grep, not against another doc. |

---

## 4. P1 — Positioning & messaging

The current `README.md` reads as a feature inventory (accurate, but undifferentiated — a
stranger can't tell in 30 seconds why this beats Amoro or a cron job). Per
`COMPETITIVE_ANALYSIS_2026.md`, Cryo's actual wedge is narrow and real: **open-source,
self-hosted, Trino-first, load-aware admission, audit-grade.** The README should lead with that,
not with the full feature list.

**Action:**
- Add a short "why Cryo, and why not X" section near the top of `README.md`:
  - vs. Amoro: lighter, Trino-first (not Flink/Spark-coupled), load-aware admission Amoro
    doesn't have.
  - vs. LakeOps: open-source and self-hosted (LakeOps is enterprise-SaaS-only today per its own
    pricing page).
  - vs. Databricks/Snowflake/S3 Tables managed optimization: for teams who aren't on one of
    those platforms, or who need on-prem/self-hosted for compliance reasons.
- Add an explicit "who this isn't for yet" line: multi-tenant SaaS operators (tenant isolation
  is opt-in, not default), Flink-primary streaming shops (engine is config-gen only), teams
  wanting a zero-config managed product (this is a thing you operate, not a SaaS).
- Consider a comparison table in the README — a public-safe, simplified version of the internal
  `COMPETITIVE_ANALYSIS_2026.md` §4 matrix. Keep it honest about gaps (Puffin stats, cost-benefit
  gating) rather than only showing favorable rows — a visibly honest gap list is itself a trust
  signal for this audience.

---

## 5. P2 — Community & contribution readiness

- `CONTRIBUTING.md` already exists and is reasonably thorough — verify its Quick Start
  (`./start.sh`, or now `./start-local.sh`) actually matches the current scripts in the repo
  root; a contributor's first experience being a broken quick-start command is an easy way to
  lose a first-time contributor.
- Add "good first issue" labels to a handful of real, scoped, low-risk backlog items from
  `todo.md` / `PREMORTEM_ACTIONABLES.md` §🟢 P2 (e.g., the `aria-label` fix, the orphaned
  `/jobs/:jobId` route link) — gives new contributors a real, low-stakes entry point.
- Decide (per `docs/GOVERNANCE.md`, which already scopes this deliberately light — no PMC/CLA
  yet) whether that's still the right call for a public launch, or whether a lightweight CLA is
  wanted given the destructive-operations nature of the tool. This is a legal/business call, not
  an engineering one — flag it, don't decide it unilaterally.
- Set up a discussion venue (GitHub Discussions is the zero-setup default) before launch, so
  day-one questions have somewhere to land besides Issues.

---

## 6. P2 — Launch logistics

- Tag an actual `v0.1.0` (or similar pre-1.0 signal) rather than launching off a bare `main` —
  pre-1.0 versioning sets correct expectations given the acknowledged "~65% dependable" honest
  self-score in `6_MONTH_PREMORTEM_ANALYSIS.md`.
- Turn on GitHub's Dependabot (or Renovate) for the open-ended-version dependencies flagged in
  §3, so drift is caught automatically going forward, not just at launch.
- Turn on branch protection on `main` (required CI checks, no direct pushes) before making the
  repo public — this is currently unclear/unverified and should be confirmed as part of the
  org-settings pass in §1.4.
- Decide who the first public-facing point of contact is for security reports
  (`SECURITY.md` needs a real email or GitHub Security Advisory routing, not a placeholder).

---

## 7. Go / No-Go Checklist

Use this as the literal final gate before flipping the repo from private to public.

Status reflects the work in commits through `160d328` on `main`. Re-verify anything marked
**[DEFERRED]** before tagging `v0.1.0`.

- [x] `LICENSE` file exists at root with real license text; `README.md` and `GOVERNANCE.md`
      agree with it — Apache-2.0 added at repo root; `README.md:243` updated; `docs/GOVERNANCE.md`
      link `../LICENSE` now resolves.
- [x] `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue templates, PR template, `CHANGELOG.md` exist
      — see `SECURITY.md`, `CODE_OF_CONDUCT.md`, `.github/ISSUE_TEMPLATE/{bug,feature,security,question}.md`,
      `.github/PULL_REQUEST_TEMPLATE.md`, `CHANGELOG.md`.
- [x] Re-verified (not just re-read) all `PREMORTEM_ACTIONABLES.md` 🔴 P0 items against current
      HEAD — see `docs/OSS_P0_REVERIFY_2026_07_07.md`. 6 of 7 GREEN by code + test evidence,
      1 requires a host with `helm` installed (see next item).
- [ ] `helm install --dry-run` (ideally a real install) succeeds against the current chart
      **[DEFERRED — needs a host with `helm` CLI]**; the chart files exist (`deploy/helm/cryo/templates/`).
- [x] Migrations squashed to a single baseline for new installs, or explicitly documented why
      not — see `docs/MIGRATIONS_DECISION.md` (decision: don't squash; preserve option for v1.0.0).
- [x] Experimental features (native Rust engine, Flink engine, MCP, cost attribution) are
      labeled as such in-app and in docs, not presented as done — see
      `cryo/engines/native/engine.py` and `cryo/engines/flink/engine.py` module docstrings;
      `cryo/mcp/server.py` warning; `/attribution` route removed from nav per `web/src/App.tsx`.
- [x] One Rust engine tree deleted (`cryo-engine` or `cryo-native` — pick one), the other kept
      and clearly scoped — `cryo-engine/` removed; `cryo-native/` kept as the active tree
      wired into `cryo/engines/native/engine.py`; documented in `docs/current-state.md`.
- [x] README rewritten to lead with the competitive wedge, not the full feature inventory —
      §4 added the **Who this isn't for (yet)** callout and the **Status (read this before
      adopting)** preamble. The existing "Why Cryo" and comparison table were already in
      place.
- [x] Full `gitleaks` scan run against entire git history, not just recent diffs — ran
      `gitleaks detect --log-opts="--all"` against all 88 commits in the local clone,
      clean.
- [x] `docs/` reviewed for anything that shouldn't be public (session logs, internal
      references) — `docs/chat-b413e9e0-build-log.md` removed; `/Users/pw/...` references
      sanitized in `agents.md` and `docs/cost-savings.md`; see §1.4 actions.
- [ ] GitHub org/repo settings confirmed (branch protection, security advisories enabled,
      Dependabot on) before visibility flips to public **[DEFERRED — requires maintainer
      with org-admin access to `shelf-project`]**.
- [ ] `v0.1.0` tag cut **[DEFERRED]**; cut immediately before flipping visibility, after the
      two `[DEFERRED]` items above are confirmed.

---

## 8. Suggested sequencing

| When | Focus |
|---|---|
| Week 1 | §1 in full (license, hygiene files, secret scrub, re-verify P0 security fixes) — nothing else matters until this is done |
| Week 2 | §2 (scope narrowing — delete/label experimental features, decide the one-Rust-engine question) + §3's migration squash and Helm validation |
| Week 3 | §4 (README/positioning rewrite) + §5 (community setup) |
| Week 4 | §6 (release logistics) + run the §7 checklist end to end + tag `v0.1.0` and flip public |

This is deliberately compressed — most of the work is deletion, labeling, and documentation
honesty, not new features. The core product doesn't need four more weeks of engineering to be
worth open-sourcing; it needs four weeks of the repo telling the truth about what it is.

---

## 9. Cross-references

| Topic | Deeper detail already lives in |
|---|---|
| Security/auth/tenancy hardening detail | `docs/PREMORTEM.md`, `docs/PREMORTEM_ACTIONABLES.md` |
| Full feature-by-feature production-readiness matrix | `docs/6_MONTH_PREMORTEM_ANALYSIS.md` |
| Competitive landscape, feature backlog vs. LakeOps/Amoro/Databricks | `docs/COMPETITIVE_ANALYSIS_2026.md` |
| Deployment/DR/backup operational detail | `docs/DEPLOYMENT.md` |
| Current governance model | `docs/GOVERNANCE.md` (needs its `LICENSE` link fixed per §1.1) |
