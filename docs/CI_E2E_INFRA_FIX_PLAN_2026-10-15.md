# Plan: fix the four CI jobs blocking every PR merge

**Status:** Implemented 2026-10-15. Hive image build verified locally. Accessibility suite verified locally (45/45). Snapshot comparison and a live native-e2e run were not re-executed here.
**Owner:** executed in-repo
**Context:** `main`'s branch protection requires 14 status checks. Four have been
red since before this investigation and are not specific to any one branch —
confirmed by reproducing all four failures with a feature branch's changes
stashed, against a clean checkout of `main`. Every PR is currently merged with
`gh pr merge --admin`, bypassing the block. This plan closes that gap.

Evidence for every finding below is a specific CI run and log line, not a
guess. Run examined: `38695324142` (2026-10-15), job IDs cited per section.

---

## 1. `web-visual` (Web visual regression) — missing `working-directory: .`

**Fails at:** `Run DB migrations`
**Error:** `FAILED: No 'script_location' key found in configuration.`
**Job:** [`116293172858`](https://github.com/tech-pw/cryo/actions/runs/38695324142/job/116293172858)

### Root cause

The `web-visual` job sets `defaults.run.working-directory: web` for every step.
Its "Run DB migrations" step is:

```yaml
- name: Run DB migrations
  run: alembic upgrade head
```

With no per-step `working-directory` override, this runs from `web/`, where
there is no `alembic.ini` — the file lives at the repo root. Alembic reports
`No 'script_location' key found` because it isn't finding the project's
`alembic.ini` at all.

The sibling job `web-e2e` has the **same step** but already overrides it:

```yaml
- name: Run DB migrations
  working-directory: .
  run: alembic upgrade head
```

`web-e2e` passes this step; `web-visual` does not. This is the entire
difference.

### Fix

Add `working-directory: .` to `web-visual`'s "Run DB migrations" step, copying
`web-e2e`'s step exactly.

```diff
       - name: Run DB migrations
+        working-directory: .
         run: alembic upgrade head
```

**File:** `.github/workflows/ci.yml`, `web-visual` job (around line 650).

### Verification

- Push a branch with only this one-line change.
- Confirm `web-visual`'s "Run DB migrations" step now succeeds and the job
  proceeds to "Start API + Vite dev server" and the Playwright run.
- The Playwright snapshots themselves may still need attention (untested past
  this point) — scope this fix to getting the job *running*, not to passing
  snapshot comparisons, which is a separate concern once the job can execute.

**Estimated effort:** 5 minutes to fix, one CI run (~5 min) to confirm.

---

## 2. `web-e2e` (Web E2E) — stale text assertion after a copy change

**Fails at:** `Run critical-path + WCAG 2.2 AA gate (axe-core)` — every single
route in the suite, identically
**Error:** `expect(locator).toBeVisible() failed` — `Locator: getByText('Datalake health').first()`, 15000ms timeout
**Job:** [`116293172920`](https://github.com/tech-pw/cryo/actions/runs/38695324142/job/116293172920)

### Root cause

The shared sign-in helper used by every spec in `accessibility.spec.ts`,
`critical-path.spec.ts`, and `smoke.spec.ts` asserts the post-login landing
page shows the text "Datalake health":

```ts
// web/e2e/accessibility.spec.ts:227 (identical pattern in the other two files)
await expect(page.getByText("Datalake health").first()).toBeVisible({ timeout: 15_000 });
```

The root route (`/`) renders `DatalakeHealth.tsx`, whose `<PageHeader>` title
is now **"Home"**:

```tsx
// web/src/pages/DatalakeHealth.tsx:132-134
<PageHeader
  title="Home"
  subtitle="Your lake at a glance. Health, maintenance and the next best action."
```

The page title was changed (part of the UI redesign work landed earlier) and
the shared e2e sign-in helper was never updated to match. Because every spec
in the suite calls this helper to sign in before testing its actual route,
**every single test in `web-e2e` fails on the same stale assertion**, not on
anything specific to the route under test. This makes the job's failure list
look like 23 unrelated accessibility violations when it is actually one stale
string in one shared helper.

"Datalake health" does still appear elsewhere in the app (the 404 page's "Go
to Datalake health" button, `TableDetailShell`'s empty state) — those are
intentional, unrelated uses of the phrase as a destination label, not the
page title. Do not change those.

### Fix

Update all three occurrences to match the real page title. Confirm the exact
visible text in the running UI before writing the replacement — `"Home"`
alone may be too generic a `getByText` match (it could match nav links,
breadcrumbs, etc.), so prefer scoping to the heading role:

```diff
- await expect(page.getByText("Datalake health").first()).toBeVisible({ timeout: 15_000 });
+ await expect(page.getByRole("heading", { name: "Home" }).first()).toBeVisible({ timeout: 15_000 });
```

**Files:**
- `web/e2e/accessibility.spec.ts:227`
- `web/e2e/critical-path.spec.ts:33`
- `web/e2e/smoke.spec.ts:13`

Grep for other stale UI-copy assertions in the same three files while in
there — this class of bug (test string drifted from a shipped copy change)
is exactly what caused this failure, and the redesign commits referenced in
this repo's recent history (`e5414f17`, `9a97cfce`, `0344b049`, and others
titled "fix(web): ..." touching page copy) are a reasonable place to check
for more of the same.

### Verification

- Run the updated specs locally against a dev server first
  (`npx playwright test e2e/accessibility.spec.ts --reporter=list`) to avoid
  burning CI minutes on a typo.
- Push and confirm all 23 previously-failing cases in `web-e2e` now pass.
- If any route still fails after this fix, it is a **real** issue (the
  stale-string failure was masking whatever is underneath) — investigate
  that route specifically rather than assuming it's the same root cause.

**Estimated effort:** 15–30 minutes (three files, plus checking for
similarly stale assertions), one CI run (~10 min) to confirm.

---

## 3. `native-e2e` / `native-compaction-e2e-go` — Hive image build fails on Debian 11 archived packages

**Fails at:** `Start MinIO, Iceberg REST, and Trino` (the step that brings up
`docker-compose.dev-datalake.yml`, which includes building `deploy/hive/Dockerfile`)
**Error:**
```
E: Failed to fetch http://deb.debian.org/debian-security/pool/updates/main/n/nghttp2/libnghttp2-14_1.43.0-1%2bdeb11u3_amd64.deb  404  Not Found
E: Failed to fetch .../libcurl4_7.74.0-1.3%2bdeb11u16_amd64.deb  404  Not Found
E: Failed to fetch .../curl_7.74.0-1.3%2bdeb11u16_amd64.deb  404  Not Found
```
**Job:** [`116293172907`](https://github.com/tech-pw/cryo/actions/runs/38695324142/job/116293172907) (`native-compaction-e2e-go`; `native-e2e` likely fails the same way but was not individually re-confirmed in this run — verify both)

### Root cause

`deploy/hive/Dockerfile` is:

```dockerfile
FROM apache/hive:4.0.0

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && curl -fsSL https://jdbc.postgresql.org/download/postgresql-42.7.4.jar \
      -o /opt/hive/lib/postgresql.jar \
    && cp /opt/hadoop/share/hadoop/tools/lib/hadoop-aws-*.jar /opt/hive/lib/ \
    && cp /opt/hadoop/share/hadoop/tools/lib/aws-java-sdk-bundle-*.jar /opt/hive/lib/ \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
```

`apache/hive:4.0.0` is built on Debian 11 ("bullseye"). Debian 11 reached end
of standard support; its packages are moving off the live `debian-security`
mirror onto `archive.debian.org`, and specific point-release package builds
(here, `libnghttp2`, `libcurl4`, `curl` at the exact deb11u-point-release
pinned by that base image's apt sources) are 404ing from the live mirror.
This is an upstream/ecosystem problem, not something introduced by this repo
— it will keep happening to any Debian-11-based image as more point releases
age out, until the base image itself is upgraded or the apt sources are
pointed at the archive.

This also means the fix is time-sensitive in the other direction: patching
around today's specific 404s (e.g. pinning `archive.debian.org` now) only
delays the next occurrence when Debian ages out further packages this
Dockerfile happens to need.

### Fix options, in order of preference

**Option A — point apt at the Debian archive mirror (fastest, most durable
for a Debian-11-based image).** Add the archive source before `apt-get
update`, since `debian-security` won't serve old point releases but
`archive.debian.org` does:

```dockerfile
FROM apache/hive:4.0.0

USER root

RUN echo "deb http://archive.debian.org/debian bullseye main" > /etc/apt/sources.list.d/archive.list \
    && echo "deb http://archive.debian.org/debian-security bullseye-security main" >> /etc/apt/sources.list.d/archive.list \
    && apt-get update -o Acquire::Check-Valid-Until=false \
    && apt-get install -y --no-install-recommends curl \
    && curl -fsSL https://jdbc.postgresql.org/download/postgresql-42.7.4.jar \
      -o /opt/hive/lib/postgresql.jar \
    && cp /opt/hadoop/share/hadoop/tools/lib/hadoop-aws-*.jar /opt/hive/lib/ \
    && cp /opt/hadoop/share/hadoop/tools/lib/aws-java-sdk-bundle-*.jar /opt/hive/lib/ \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
```

`Acquire::Check-Valid-Until=false` is required because archived Debian
releases carry an expired `Valid-Until` date in their Release file by design;
apt refuses the repo otherwise.

**Option B — check for a newer `apache/hive` tag built on a current Debian/
Ubuntu base.** If `apache/hive` publishes a tag on Debian 12 ("bookworm") or
later, moving to it sidesteps this whole class of problem going forward.
Check Docker Hub / the Apache Hive release notes for what base OS later tags
use before committing to this — it may require re-verifying the rest of the
Dockerfile (paths under `/opt/hadoop`, `/opt/hive` could differ by version).

**Option C — vendor the two artifacts instead of apt-installing curl.**
`curl` here is only a build-time tool to fetch one JAR; the final image does
not need it (it's `apt-get purge`d immediately after). Could use a
multi-stage build with a `curl`-capable stage unaffected by this Dockerfile's
own base image, or fetch `postgresql-42.7.4.jar` via a GitHub Action step
(`curl` runs on the *runner*, which has current packages) and `COPY` it in,
removing the need for `apt-get install curl` in this Dockerfile at all.

**Recommendation:** Option A first (smallest diff, unblocks CI today).
Evaluate Option C as a follow-up, since it actually removes the Debian-11
apt dependency from the image build rather than working around today's
instance of it — worth doing once CI is green and there's no time pressure.

### Verification

- Build `deploy/hive/Dockerfile` locally: `docker build -f deploy/hive/Dockerfile .`
- Confirm `docker compose -f docker-compose.dev-datalake.yml up -d minio minio-init iceberg-rest trino` succeeds end-to-end in CI.
- Confirm both `native-e2e` and `native-compaction-e2e-go` get past the
  "Start MinIO, Iceberg REST, and Trino" step. Re-check `native-e2e`
  specifically — it was not individually re-confirmed to fail the same way
  in this investigation; verify before assuming the same fix covers it.
- Downstream pytest suites in these jobs were never reached in the runs
  examined — once this step passes, those suites are an unknown. Treat
  getting the jobs *running* as this fix's scope; triage any pytest failures
  that surface as a separate, follow-up problem.

**Estimated effort:** 30–60 minutes (Option A is a small diff, but confirming
the archive mirror actually serves everything `apt-get install curl` pulls in
transitively needs a real CI run, not just a guess), one to two CI runs
(~10–15 min each) to confirm.

---

## Summary table

| # | Job | Root cause | Fix size | Confidence |
|---|---|---|---|---|
| 1 | `web-visual` | Missing `working-directory: .` on one step | 1 line | High — exact diff against a passing sibling job |
| 2 | `web-e2e` | Stale `"Datalake health"` string vs. shipped `"Home"` title, in a shared helper | 3 files, ~3 lines | High — confirmed by reading the actual component |
| 3 | `native-e2e` / `native-compaction-e2e-go` | `apache/hive:4.0.0`'s Debian 11 base has aged-out security packages | Dockerfile change | Medium — root cause is solid, but which fix option lands best needs a real CI run to confirm the archive mirror covers everything needed |

**Suggested order:** #1, then #2 (both are small, high-confidence, and
independent of each other — could be one PR or two). Do #3 separately,
since it needs its own verification cycle and the archive-mirror
check-valid-until flag is the kind of change worth a reviewer's eyes before
merging into a workflow every PR depends on.

Once all three are confirmed green on a branch, no further branch-protection
change is needed — the four currently-failing required checks will simply
start passing, and `--admin` overrides stop being necessary to merge.

---

## Execution notes (2026-10-15)

Re-checked run `38695324142` before editing. `native-e2e-go` was already green.
`native-e2e` did **not** fail on the Hive image: it reached pytest and failed
with `failed to commit expire_snapshots transaction: Unexpected => An unknown
server-side problem occurred; the commit state is unknown.` The fixture has 3
snapshots and `retain_last=1`. The Go engine already expires one snapshot per
REST commit (`engine-go/internal/ops/expire.go`); Rust committed them all at
once. Rust execute mode now steps the same way. Analyze/preview is unchanged.

`web-e2e` was not only the stale title. `accessibility.spec.ts` failed on
`getByText("Datalake health")` because onboarding is dismissed there and the
home title is `Home`. `critical-path.spec.ts` passed that assertion (the
first-run wizard's "Review datalake health" is a case-insensitive substring
match) and then timed out on a `Connections` nav link that no longer exists.
Sign-in helpers now assert heading `Home`. Critical-path and smoke follow the
current nav (Settings, Tables, Runs, Scanner) and dismiss onboarding so the
shell is usable.

After the sign-in fix, two accessibility routes were real axe failures, not
stale copy:

- `/ai` — link in text with no underline (`settings/homes.tsx`).
- `/smart-maintenance` — warning badge text `#a9791e` on the composited badge
  fill `#eae8de` (3.14:1). Badge warning text now uses `--primitive-warning-fg`
  (`#7a5614`). The decorative warning primitive is unchanged.

`web-visual` migrations and the API start step both set `working-directory: .`
(the API step had the same missing override as migrations; `web-e2e` already
had it). Visual sign-in and the health/operations heading assertions were
updated so the job can reach screenshot comparison. Snapshot baselines were
not regenerated. Login snapshots may still pass; authenticated snapshots are
likely stale after the home redesign and are still out of scope.

Hive: rewriting existing apt sources onto `archive.debian.org` (and dropping
`bullseye-updates`) is what unblocks install. Appending an archive list
without replacing the live mirror still 404s, because apt keeps the failing
URI. `docker build -f deploy/hive/Dockerfile .` succeeded and fetched the
previously 404ing `libnghttp2` / `libcurl4` / `curl` deb11u point releases
from the archive. `native-e2e` and `native-e2e-go` do not build Hive; only
`native-compaction-e2e-go` starts Trino, which depends on `hive-metastore`.
