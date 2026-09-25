# OSS-0 baseline: release boundary and evidence checklist

Date: 2026-09-25. Original candidate commit: `231c799d4e0899f5c1d0e9ce9bcec059cd5a5740`
(branch `gitlab-main`). Tracks the OSS-0 workstream in
[the OSS launch plan](oss-launch-plan-2026-09-25.md). Status: **in progress — see open items.**

**Note on this document's history:** this checklist and its accompanying working-tree changes
were originally produced against `231c799d`, then lost to an uncommitted-changes wipe when a
concurrent process reset the `gitlab-main` working tree while this doc was still unstaged (see
`git reflog` around `HEAD@{2}`–`HEAD@{4}` on 2026-09-25 for the reset). This is a redo, committed
immediately this time so it survives further concurrent resets. If working in this repo
alongside another agent/session, commit OSS-0 changes promptly rather than leaving them
uncommitted in the working tree.

## What this covers

OSS-0's done condition per the launch plan: "one owned checklist names every public artifact
and current blocker; no unresolved sensitive-material exposure is included in the release
candidate; source and artifact attribution is reviewed; the private reporting path works."

## Findings and actions taken

| Item | Finding | Action |
|---|---|---|
| Agent scratch state | `.kilo/` (87MB local, 1 file tracked: a codebase-rating plan) and `.cursor/plans/` are AI-agent working state, not project source. | Untracked the one file; added both to `.gitignore`. |
| PW-internal operational docs | `docs/PDAE-*.md` (4 files, internal Jira-ticket writeups), `docs/incidents/` (1 RCA with real prod catalog/table names and Slack timestamps) were in the public `docs/` tree. No literal secrets found in them, but they document PW-specific incidents, internal pipeline names, and ticket IDs that don't belong in a public release. | Moved to `docs/_private-pw/` and gitignored that path so it stays local-only going forward. Git history still contains these files at their old path — see "History" below. |
| Secrets/credentials scan | Ran `gitleaks detect` against the full working tree and all 732 commits of history (at the original `231c799d` pass). 6 findings, all the same false positive (`o.enableSigv4`, an upstream Apache iceberg-go identifier misidentified as a generic API key) across different commits of the same unchanged line. No real credentials, keys, or tokens found in tracked files or history. | No action needed. Re-run before the actual public-visibility flip, since history keeps growing — including the additional commits (`cbc857e`, `ac0545a`) landed since this pass. |
| Security disclosure routing | `SECURITY.md` points to a GitHub Security Advisory form (`github.com/shelf-project/Cryo/security/advisories/new`). Confirmed via `gh repo view`: **the repo is currently private** (`isPrivate: true`). GitHub only exposes that advisory form to users with at least read access to a private repo — an outside reporter cannot reach it today. | **Open.** This is expected pre-launch but must be re-verified live, by an account with no special access, once the repo is flipped public — before advertising the disclosure route anywhere external. |
| Third-party provenance — vendored Iceberg Go | `engine-go/third_party/iceberg-go-v0.6.0-patched/` is a full copy of `apache/iceberg-go` v0.6.0 (Apache-2.0). `LICENSE` and `NOTICE` are retained; `CRYO_PATCH.md` documents all 4 patches with rationale, reproduction, and an explicit "remove this fork" exit plan once upstream lands the fixes. | Attribution is sound. Redacted internal ticket IDs (formerly `PDAE-8279`, `PDAE-8353`) and one real production table name from `CRYO_PATCH.md`'s patch rationale, keeping the technical explanation intact. |
| Distributed JARs | No `.jar` files found anywhere in the tracked tree. Launch plan's "distributed JARs" provenance concern does not currently apply — re-check if any get added before release. | No action needed today. |
| Candidate CI state | GitHub `origin/main` was at `a8915bd6`, stale by 253 commits behind the actual candidate branch `gitlab-main` (`231c799d`). The two histories were a clean fast-forward (no divergence/rewrite risk). | **Resolved (2026-09-25).** Pushed `gitlab-main` to GitHub `main` (`a8915bd..231c799`, fast-forward, with explicit user confirmation before pushing). GitHub `main` has since advanced further as `gitlab-main` gained new commits (`cbc857e` discarded by a concurrent reset, then `ac0545a` committed fresh) — re-sync and re-check CI status before treating this as closed for the actual final candidate commit. |

## Open blockers (not yet closed)

1. **Security disclosure route is unverified in the state it'll actually be used** (public repo,
   unauthenticated reporter). Re-test after the visibility flip.
2. **`.kilo/`-style scratch dirs may reappear** — the gitignore now excludes them, but nothing
   currently enforces that in CI (e.g., no check that blocks a PR from re-adding tracked files
   under `.kilo/` or `docs/_private-pw/`). Consider a CI lint step if this becomes a recurring risk.
3. **git history still contains the PW-internal docs at their old `docs/` path** (pre-move
   commits). Per the launch plan, history rewriting is "a deliberate maintainer action, never an
   incidental rewrite in a packaging PR" — explicitly not done here. If a full public history
   is published as-is, those old commits remain readable. Decide (later, deliberately) whether
   the public release ships full history or a squashed/rebased public history.
4. **GitHub `main` needs re-sync and CI re-verification** against whatever commit is the actual
   final candidate — `gitlab-main` moved again (see note above) after the original CI-sync push.

## Closed since first pass

- `CRYO_PATCH.md` internal-reference redaction: done (redone in this pass after the wipe).
- Original CI/candidate-commit staleness against `231c799d`: resolved by the fast-forward push;
  superseded by open item #4 above since `gitlab-main` has since moved.

## Not yet started (rest of OSS-0 scope)

- Full inventory of *every* public artifact (this pass covered docs, secrets, and the vendored
  Go dependency — not yet: Helm chart contents, Docker image contents, Python/JS dependency
  license audit, `.github/workflows/*` for hardcoded PW-specific values).
- License/NOTICE audit of the rest of the dependency tree (Python, JS, Rust/Go engine deps
  beyond the one vendored copy already reviewed).

## Re-verification note

This checklist reflects a point-in-time review, originally of commit `231c799d`, redone after
an uncommitted-work wipe with awareness that `gitlab-main` has since advanced. Per the launch
plan, rerun the secrets scan and CI before treating any later commit as the release candidate —
this applies with extra force here given the concurrent-write history above.

## Working-in-this-repo note for future agents/sessions

`gitlab-main` had at least one other concurrent process committing to it during this OSS-0 pass
(a Spark-cost-reservation fix landed as `ac0545a`, after an intermediate commit `cbc857e` was
reset away). If you're picking up OSS-0/1/2/3/4/5 work here: **commit your changes as soon as
they're coherent, rather than leaving them staged/unstaged in the working tree** — an
uncommitted change has no protection from a concurrent `git reset` on the same checkout.
