# Cryo — Org-Specific Assumptions Audit

> Audit of every place in the codebase that hardcodes an organization-specific
> name, endpoint, credential, bucket, account, or example data. The goal is for
> this repo to be open-source-clean: any contributor from any organization can
> clone, configure, and run it without inheriting a previous tenant's identity.

This is the companion to `docs/OSS_READINESS.md` — that doc covers licensing,
visibility, governance, and community process. **This doc covers code hygiene:
where the repo pretends to be one tenant's repo when it should be a neutral
platform.**

---

## 0. Methodology

Two passes:

1. **String scan** for org-specific names across all source-controlled files
   (excluding `.venv/`, `node_modules/`, `cryo-native/target/`, generated
   dist-info).
2. **Config scan** for endpoints, account IDs, bucket names, table names, user
   emails, and credentials that are unique to one tenant.

Findings are graded:

- 🔴 **Must-fix before OSS** — visible branding, contact, or endpoint that ties
  the repo to one org.
- 🟡 **Should-fix** — historical reference that should be neutralized before
  flip, but doesn't break a fork.
- 🟢 **OK** — internal-only, harmless, or already generic.

---

## 1. Findings

### 1.1 Repository identity

| Severity | Where | Current | Required |
|---|---|---|---|
| 🔴 | `README.md:10`, `:147`, `:274` | `https://github.com/the previous GitHub org` | Replace with the **actual public** org URL. If the public org is decided to be different from `the previous org`, change once across the repo. |
| 🔴 | `CONTRIBUTING.md:34`, `:131` | `https://github.com/the previous GitHub org.git` | Same. |
| 🔴 | `CHANGELOG.md:28-29` | compare/release links to `the previous GitHub org` | Same. |
| 🔴 | `SECURITY.md:24`, `:60` | `security@the previous org.example` + GitHub advisory URL | Replace with real address once the public repo is named. `the previous org.example` is reserved per RFC 2606 — it won't deliver mail. |
| 🔴 | `docs/GOVERNANCE.md:3`, `:38` | "GitHub-first governance under `the previous org` is sufficient" | Rewrite for whatever the public org name is, OR drop the org reference and speak only in terms of "current maintainers" / "the maintainer team". |
| 🔴 | `docs/OSS_READINESS.md` (entire doc) | Repeatedly references `the previous GitHub org` and "the previous org admin" | Replace with placeholders (`<org>/Cryo`, `<your-org-admin>`) or use the public org name once decided. |
| 🔴 | `docs/operator-guide.md:4-7`, `docs/research-and-roadmap.md:4-9`, `docs/GOVERNANCE.md:4` | GitHub blob links hardcoded to `the previous GitHub org` | Same. |
| 🔴 | `.github/ISSUE_TEMPLATE/question.md:9`, `.github/ISSUE_TEMPLATE/security.md:10` | GitHub Discussions + Security Advisories URLs hardcoded | Same. |

**Resolution rule:** before the public flip, replace every `the previous GitHub org`
with the chosen public org URL in a single sweep (script: `grep -rl the previous org
--exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=cryo-native/target |
xargs sed -i ''`). The audit doc itself gets `<org>/Cryo` placeholders.

### 1.2 Internal endpoints, buckets, accounts

The runtime code is **clean** — no previous-tenant hosts, no production coordinator hostnames,
no hardcoded prod bucket names, no internal account IDs in source. The
Trino/Hive endpoints that the user previously hit from this dev box were
configured in the UI (Connections page) and stored in `cryo.sources` /
`cryo.destinations`, not in code.

The only org-shaped strings in runtime code are **test fixtures**:

| Severity | Where | Current | Required |
|---|---|---|---|
| 🟢 | `tests/test_list_all_namespaces.py` | Fixture catalog names | Rechecked 2026-10-06: no production catalog prefix remains in `tests/`. |
| 🟡 | `web/src/pages/AwsIntegrations.test.tsx`, `web/src/pages/CostInsights.test.tsx` | Source named `prod-lakehouse`, bucket `prod-iceberg` | These are user-visible strings in mocked API responses. Replace with `sample-lakehouse` / `lake-bucket` to make it obvious they're not real prod. |
| 🟢 | `docs/research-and-roadmap.md`, `docs/architecture.md` | Worked examples | Rechecked 2026-10-06: those files do not name a production catalog. |

### 1.3 User names / contact info

| Severity | Where | Current | Required |
|---|---|---|---|
| 🟢 | `AGENTS.md` (this repo) | No author names. | Keep clean. |
| 🟢 | `.git/` history | `git config user.email "cryo@local"` was set by automation during this session; no real PII committed. | OK — automated agent, not a human committer. |
| 🟢 | All code | No staff mailboxes or first-person attribution. | OK. |

### 1.4 Default ports, hosts, credentials in dev defaults

| Severity | Where | Current | Required |
|---|---|---|---|
| 🟢 | `docs/operator-guide.md:75-85` | `localhost:8080`, `localhost:9000`, etc. | These are the standard local-stack defaults (Trino, MinIO, Postgres, Redis, Hive Metastore, Prometheus, Grafana, Cryo). Generic dev defaults, not org-specific. Keep, but split out into a clearly-labeled "Local dev only" table. |
| 🟢 | `start-local.sh`, `docker-compose*.yml` | Same generic dev defaults; uses `cryo/cryo` Postgres user | OK — local dev only; explicitly tracked in `docs/LOCAL_DEV_ONLY_INVENTORY.md`. |
| 🟢 | Default credentials in compose | `minioadmin/minioadmin`, `admin/admin` | OK — universal dev defaults, called out as such in `operator-guide.md`. |

### 1.5 Sample data, demo seed data

| Severity | Where | Current | Required |
|---|---|---|---|
| 🟢 | `scripts/seed_local_config.py`, `scripts/create_test_tables.py`, `scripts/load_test_data.py` | Creates locally-named sources/destinations like `minio-local`, `rest-local` | OK — generic dev naming. |
| 🟢 | `tests/` fixtures | Schema names | Rechecked 2026-10-06: no production schema name remains in test fixtures. |

### 1.6 Email addresses and security contact

| Severity | Where | Current | Required |
|---|---|---|---|
| 🔴 | `SECURITY.md:24` | `security@the previous org.example` | RFC 2606 reserved TLD — doesn't deliver mail. Replace with a real address at the public org's domain (or use `security@<org>.org` as a placeholder that's clearly meant to be edited). |

### 1.7 Documentation tone and references

| Severity | Where | Current | Required |
|---|---|---|---|
| 🟡 | `docs/research-and-roadmap.md`, `docs/architecture.md` | Some passages discuss "our current prod setup" with internal table names | Editorial pass needed — convert "we" / "our prod" to "the platform" / "a typical deployment". |
| 🟢 | `README.md` | No internal product names. | Keep clean. |

---

## 2. Summary

**Headline:** the source code itself is clean of org-specific assumptions — the
runtime reads all endpoints, credentials, and buckets from the `cryo.sources` /
`cryo.destinations` tables and from environment variables via
`cryo.config.Settings`. **The leakage is entirely in repository-identity strings
(§1.1) and a few test fixtures (§1.2).**

That's the right shape for OSS: a contributor can clone the repo, run
`make up-datalake && make up-cryo && make seed`, and never encounter a
previous-tenant or old-org URL or endpoint.

### Action checklist before public flip

- [x] Public org is `tech-pw/cryo` (still private until the flip)
- [x] Public identity is `https://github.com/tech-pw/cryo` (still private until the flip)
- [x] `SECURITY.md` no longer publishes a reserved `.example` mailbox. Disclosure is the
      GitHub advisory form, which is unreachable until the repo is public.
- [x] `prod_data` is not in `tests/`. Fixture names stay fictional.
      `sample_<schema>` in `tests/test_list_all_namespaces.py`
- [ ] Rename `prod-lakehouse` / `prod-iceberg` in
      `web/src/pages/AwsIntegrations.test.tsx` and `CostInsights.test.tsx`
- [ ] Editorial pass on `docs/research-and-roadmap.md` and
      `docs/architecture.md` to remove "we" / "our prod" / specific prod table
      names

### Anti-assumption rule (now in `AGENTS.md`)

From this point forward, **no new file may introduce an org-specific
identifier** (org/repo name, endpoint, bucket, account, email, sample data
shape that mirrors one tenant). See `AGENTS.md` §10 for the full rule.

---

## 3. What we deliberately *didn't* do

- **Did not scrub git history.** GitHub's fork graph preserves history; rewriting
  it is anti-community and frequently cited as a red flag in OSS reviews. The
  audit covers *current* content; old commits stay as-is.
- **Did not replace `start-local.sh`'s `localhost` defaults.** Those are
  universal local-stack defaults. The org-specific data lives in the UI /
  database, not in this script.
- **Did not rename existing screenshots in docs/.** Screenshots already in
  the repo that show internal table names are a separate cleanup task and
  don't affect a fresh clone.