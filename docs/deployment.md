# Deployment — data-platform-cluster

How code changes actually get from a local edit to running in production on
`data-platform-cluster`. This is the real, battle-tested sequence — every step
here has been exercised live, including the failure modes called out inline.

This doc is GitLab-only (it names the real registry, cluster, and namespace).
Do not port it to the GitHub mirror.

## Overview

```
edit code → commit → push to GitHub main → merge into gitlab-main → push to GitLab
                                                                          |
                                                                          v
                                                        docker buildx build --push (multi-arch)
                                                                          |
                                                                          v
                                                        kubectl rollout restart (+ helm upgrade
                                                                                  if values changed)
```

Two independent things move separately and people conflate them:

- **Source code** goes through the dual-repo git flow (below).
- **The running cluster** only changes when you build+push an image and then
  either restart the rollout or run `helm upgrade`. Merging to `gitlab-main`
  does **not** deploy anything by itself — there is no CI/CD pipeline wired up
  yet. Every deploy in this doc is a manual, human-triggered action.

## Prerequisites

- `infra` CLI configured and able to mint cluster credentials (see
  [Cluster access](#cluster-access) below).
- Docker Desktop with the `multiarch` buildx builder available:
  ```bash
  docker buildx ls   # look for a builder named "multiarch"
  docker buildx use multiarch
  docker buildx inspect --bootstrap
  ```
  If it doesn't exist yet: `docker buildx create --name multiarch --use`.
- Already logged in to `registry.gitlab.com` (`docker login registry.gitlab.com`).
- Git remotes `origin` (GitHub) and `gitlab` (GitLab) both configured.

## Dual-repo git workflow

GitHub (`origin`, branch `main`) is the sanitized, open-source mirror — no real
hostnames, bucket names, or credentials. GitLab (`gitlab`, branch `gitlab-main`)
is the full-detail internal repo — it's the only place `values-dev.yaml`,
`values-stage.yaml`, and `values-prod.yaml` are tracked at all (see their
`.gitignore` note below).

For a change that's safe to share (no org-specific detail):

```bash
# 1. Commit on whichever branch you're on.
git add <files>
git commit -m "..."

# 2. Get it onto GitHub main.
git checkout main
git cherry-pick <commit-sha>          # or merge, if you branched from main

# 3. ALWAYS fetch before pushing -- teammates push to main directly and
#    conflicts here are common. Merge their commits in, don't force-push.
git fetch origin main
git log --oneline origin/main ^main   # see what's new
git merge origin/main --no-edit       # safe, clean 3-way merge in practice
git push origin main

# 4. Bring main's history into gitlab-main and push to GitLab.
git checkout gitlab-main
git merge main --no-edit
git push gitlab gitlab-main
```

For a change that contains real infra detail (values files, anything with a
real hostname/bucket/credential) — commit directly on `gitlab-main` and never
cherry-pick it to `main`.

**Before pushing anything to `origin/main`, check what's already there.**
Multiple people push to this repo; a `git push` without a fresh `git fetch`
first will get rejected (or worse, silently diverge) if someone else landed a
commit in the meantime. Read the diff of what's new before merging it in —
don't blindly `git merge` without knowing what you're pulling in.

### Why `values-*.yaml` only exist on `gitlab-main`

`main`'s `.gitignore` has a blanket `deploy/helm/cryo/values-*.yaml` rule.
`gitlab-main`'s `.gitignore` adds negation exceptions (`!values-dev.yaml` etc.)
that exist *only* on that branch. Checking out `main` makes these files
literally vanish from disk — that's intentional, not a bug.

## Cluster access

Every `kubectl`/`helm` command against this cluster requires switching context
first:

```bash
infra use data-platform-cluster
```

Do this before *every* `kubectl` command, not just the first one in a
session — the credential token this mints is short-lived. If a `kubectl`
command hangs or times out for no obvious reason (including trivial ones like
`kubectl get pods`), the most likely cause is an expired token or the
`infra.penpencil.co` token-minting service being briefly unreachable, **not**
a problem inside the cluster. Re-run `infra use data-platform-cluster` and
retry — this has resolved every instance of this symptom seen so far.

## The easy way: `deploy/deploy.sh`

Everything below this section — build, pin the digest into `values-prod.yaml`, commit,
`helm upgrade`, rollout restart, verify — is automated by `deploy/deploy.sh`:

```bash
deploy/deploy.sh                # full sequence, prompts nothing, does everything
deploy/deploy.sh --build-only    # build + pin + commit, stop before touching the cluster
deploy/deploy.sh --skip-tests    # skip the local test suite gate (not recommended)
deploy/deploy.sh --no-commit     # pin the digest locally without git commit/push
```

Written 2026-08-10 after a real incident: hand-editing `values-prod.yaml`'s three digest
fields (`image.digest`, `migration.image.digest`, `native.image.digest` — they must always
point at the same build) missed one of them on a manual edit, which would have left
api/worker/governor/scheduler running one commit behind the native engine binary shipped in
the very same image. The script captures the digest programmatically from
`docker buildx build --metadata-file` (never eyeballed from terminal scrollback) and pins it
via `deploy/pin_digest.py`, which refuses to write anything unless it finds and updates
exactly those three fields — a partial update is a hard error, not a silent skip. It also
verifies at the end that every pod actually landed on the new digest, via `kubectl get pods
-o json`, rather than trusting `rollout status` alone.

The manual sequence below still works and is useful for understanding *why* each step
exists (or if the script's assumptions about this repo's layout ever stop holding) — but for
an actual deploy, prefer the script.

## Building and pushing the image

**Always build multi-arch.** The cluster's node pool is a mix of `amd64` and
`arm64` nodes (confirm with `kubectl get nodes -o custom-columns=NAME:.metadata.name,ARCH:.status.nodeInfo.architecture`).
A plain `docker build` on an Apple Silicon Mac produces an `arm64`-only image.
It will push successfully, `helm upgrade`/`kubectl rollout restart` will
succeed, and pods will crash-loop with `exec format error` the moment they
land on an `amd64` node — this has actually happened and took down
api/worker/governor across the fleet for ~15 minutes before being caught and
fixed forward with a proper multi-arch rebuild. Always use `buildx`:

```bash
docker buildx use multiarch

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t registry.gitlab.com/penpencil-services/data/data-engineering/cryo:latest \
  -f Dockerfile \
  --push \
  .
```

This builds and pushes a single multi-arch manifest in one step (no separate
`docker push` needed). Expect this to take several minutes — cross-compiling
the non-native platform runs under QEMU emulation and is noticeably slower
than a native build. If the build fails with "no space left" or similar,
Docker's build cache has likely filled up:

```bash
docker builder prune -f
docker image prune -f
```

## Rolling out

The image tag is static (`:latest`). This means Kubernetes sees **no spec
diff** on a plain `helm upgrade` after a new image push — nothing will
actually restart. Always follow an image push with an explicit rollout
restart of every affected deployment:

```bash
kubectl rollout restart deployment/cryo-api deployment/cryo-worker deployment/cryo-governor -n cryo
# add deployment/cryo-web, deployment/cryo-scheduler, deployment/cryo-scanner-worker
# if those images/code paths changed too

kubectl rollout status deployment/cryo-api -n cryo --timeout=180s
kubectl rollout status deployment/cryo-worker -n cryo --timeout=180s
kubectl rollout status deployment/cryo-governor -n cryo --timeout=180s
```

If a rollout reports pods stuck (not reaching `Running`/`1/1`), check
immediately — don't assume it'll resolve itself:

```bash
kubectl get pods -n cryo -l app.kubernetes.io/component=api
kubectl logs -n cryo <pod-name>            # current container
kubectl logs -n cryo <pod-name> -p         # previous container, if it crashed
```

`CrashLoopBackOff` with `exec format error` in the logs means the multi-arch
build step was skipped — rebuild properly and `kubectl rollout restart` again.
`kubectl rollout undo` does **not** help here: the previous ReplicaSet
references the same mutable `:latest` tag, so "rolling back" just re-pulls
the same broken image. The only fix is pushing a corrected image and
restarting again.

## When you also need `helm upgrade`

Only needed when `values.yaml` / `values-prod.yaml` itself changes — new env
vars, resource limits, replica counts, image repository path, secrets, etc.
Pure application code changes (the common case) only need the build+push+
rollout-restart sequence above.

```bash
helm upgrade --install cryo deploy/helm/cryo \
  -n cryo \
  -f deploy/helm/cryo/values.yaml \
  -f deploy/helm/cryo/values-prod.yaml
```

Known gotchas in this chart, already fixed once but worth knowing if they
resurface:

- `imagePullSecrets` must be a **plain string list** (`- cryo-registry`), not
  a list of `{name: ...}` objects — the chart's helper template expects
  strings and silently mis-renders (`map[name:cryo-registry]` as a literal
  string) if given objects.
- `global.envFromSecret` must be set in `values-prod.yaml` or the DB
  connection silently falls back to `localhost:5432` inside the pod.
- A prior imperative `kubectl patch`/`kubectl scale` against a resource Helm
  manages can leave a field-ownership conflict that makes the next
  `helm upgrade` fail confusingly. If so, delete the conflicting resource
  (e.g. a stateless CronJob) rather than reaching for `helm upgrade --force`,
  which risks replacing more than intended.

## Verifying a deploy actually worked

Don't stop at "the pods are Running." Confirm the change is live:

```bash
# Zero restarts is the bar, not just Ready 1/1 -- a pod can be Running while
# still crash-looping through its startup probe.
kubectl get pods -n cryo -l app.kubernetes.io/component=api

# Hit the actual endpoint you changed, from inside a pod, using a short-lived
# minted token rather than the disabled/gated public auth endpoint:
API_POD=$(kubectl get pods -n cryo -l app.kubernetes.io/component=api -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n cryo "$API_POD" -- python3 -c "
from cryo.security.jwt_tokens import issue_access_token
import urllib.request
token = issue_access_token(subject='operator', roles=['admin'], ttl_hours=1)
req = urllib.request.Request('http://localhost:8000/api/v1/<path>', headers={'Authorization': f'Bearer {token}'})
print(urllib.request.urlopen(req).status)
"
```

Never print a minted token or any value read from `destinations.config` /
`sources.config` to conversation output or logs — those blobs embed real
credentials (S3 keys, Trino passwords) alongside routing config, including in
nested fields a naive top-level filter will miss. Pull only the specific
fields you need.

## Testing against real Postgres before deploying

The unit test suite runs against in-memory SQLite, which is **more lenient**
than Postgres in ways that matter — e.g. it doesn't enforce strict `GROUP BY`
column-matching the way Postgres does. A query can pass every test here and
still 500 in production. Before shipping a nontrivial SQL change, verify it
against a real Postgres:

```bash
docker compose -f docker-compose.yml up -d postgres
alembic upgrade head          # only needed once per fresh volume
.venv/bin/python3 -m pytest tests/test_your_file.py -v
```

If you add a regression test for a Postgres-specific bug, gate it behind a
fixture that skips cleanly when no local Postgres is reachable (see
`tests/test_datalake_dashboard_repository.py::postgres_session` for the
pattern) — don't make the whole suite depend on a running database.
