#!/usr/bin/env bash
# Build, pin, and roll out a new Cryo image.
#
# Required for a cluster rollout: CRYO_KUBE_CONTEXT.
# Optional: CRYO_IMAGE_REGISTRY (default ghcr.io/tech-pw/cryo),
# CRYO_VALUES_FILE, CRYO_GIT_REMOTE, CRYO_GIT_BRANCH.
#
# Written 2026-08-10 after a real incident: manually updating the overlay's three
# digest fields (image, migration.image, native.image) by hand missed one of them, which
# would have left api/worker/governor/scheduler running one commit behind the native engine
# binary shipped in the exact same image build. This script exists to make that class of
# mistake structurally impossible -- one digest, captured programmatically from the build
# itself (not eyeballed from terminal scrollback), written to all three places atomically by
# deploy/pin_digest.py (which refuses to proceed unless it finds and updates exactly 3).
#
# Usage:
#   deploy/deploy.sh                  # build, pin, commit+push, helm upgrade, rollout restart
#   deploy/deploy.sh --build-only      # build + pin + commit, stop before touching the cluster
#   deploy/deploy.sh --skip-tests      # skip the local test suite gate (not recommended)
#   deploy/deploy.sh --no-commit       # pin the digest locally but don't git commit/push
#
# See docs/deployment.md for the full manual sequence this automates and the reasoning
# behind each step (multi-arch requirement, why a rollout restart is required even though
# the image tag is static, etc).

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
REPO_ROOT="$(pwd)"
IMAGE="${CRYO_IMAGE_REGISTRY:-ghcr.io/tech-pw/cryo}"
VALUES_FILE="${CRYO_VALUES_FILE:-deploy/helm/cryo/values.yaml}"
GIT_REMOTE="${CRYO_GIT_REMOTE:-origin}"
GIT_BRANCH="${CRYO_GIT_BRANCH:-main}"

BUILD_ONLY=false
SKIP_TESTS=false
NO_COMMIT=false
for arg in "$@"; do
  case "$arg" in
    --build-only) BUILD_ONLY=true ;;
    --skip-tests) SKIP_TESTS=true ;;
    --no-commit) NO_COMMIT=true ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

log() { echo "==> $*"; }

if [[ "$SKIP_TESTS" == false ]]; then
  log "Running the test suite (skip with --skip-tests, not recommended before a prod deploy)"
  .venv/bin/python -m pytest tests/ -q --ignore=tests/integration \
    --deselect tests/test_fleet_trends_api.py::test_aggregate_fleet_health_trend_groups_by_day \
    --deselect tests/test_fleet_trends_api.py::test_aggregate_fleet_cost_trend_sums_daily_totals
  log "Tests passed"
fi

log "Checking for a dirty working tree"
if [[ -n "$(git status --porcelain -- ":!$VALUES_FILE")" ]]; then
  echo "error: working tree has uncommitted changes outside $VALUES_FILE." >&2
  echo "Commit or stash them first -- this script commits $VALUES_FILE on your behalf" >&2
  echo "and a dirty tree elsewhere makes that commit's diff misleading." >&2
  git status --short -- ":!$VALUES_FILE" >&2
  exit 1
fi

log "Ensuring the multiarch buildx builder is selected"
docker buildx use multiarch

log "Building + pushing (multi-arch: linux/amd64, linux/arm64) -- this takes several minutes"
METADATA_FILE="$(mktemp)"
trap 'rm -f "$METADATA_FILE"' EXIT

# --provenance=false: since 2026-09-30 GitLab's registry rejects the manifest list when buildx
# attaches provenance attestations ("blob unknown to registry" for a child manifest), for both
# this image and the web image. The image content is identical without them.
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --provenance=false \
  -t "${IMAGE}:latest" \
  -f Dockerfile \
  --push \
  --metadata-file "$METADATA_FILE" \
  .

DIGEST="$(python3 -c "
import json
with open('$METADATA_FILE') as f:
    meta = json.load(f)
digest = meta.get('containerimage.digest')
if not digest:
    raise SystemExit('no containerimage.digest in buildx metadata output')
print(digest)
")"
log "Built digest: $DIGEST"

log "Pinning the digest into $VALUES_FILE (image, migration.image, native.image)"
python3 deploy/pin_digest.py "$DIGEST" --file "$VALUES_FILE"

if [[ "$NO_COMMIT" == false ]]; then
  log "Committing $VALUES_FILE"
  git add "$VALUES_FILE"
  git commit -m "deploy: pin image digest $DIGEST

Built + pushed via deploy/deploy.sh from commit $(git rev-parse --short HEAD~1 2>/dev/null || echo unknown).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
  log "Pushing to ${GIT_REMOTE} ${GIT_BRANCH}"
  git push "$GIT_REMOTE" "$GIT_BRANCH"
fi

if [[ "$BUILD_ONLY" == true ]]; then
  log "Build-only requested (--build-only) -- stopping before touching the cluster."
  log "Digest $DIGEST is pinned and committed. Run without --build-only to deploy it."
  exit 0
fi

if [[ -z "${CRYO_KUBE_CONTEXT:-}" ]]; then
  log "CRYO_KUBE_CONTEXT is not set. Stopping before the cluster."
  exit 1
fi
log "Switching kube context to ${CRYO_KUBE_CONTEXT}"
infra use "$CRYO_KUBE_CONTEXT"

log "Running helm upgrade"
helm upgrade --install cryo deploy/helm/cryo -n cryo \
  -f deploy/helm/cryo/values.yaml -f "$VALUES_FILE"

log "Image tag is static (:latest) -- an explicit rollout restart is required or nothing picks up the new digest"
kubectl rollout restart deployment/cryo-api deployment/cryo-worker \
  deployment/cryo-governor deployment/cryo-scheduler -n cryo

log "Waiting for rollouts to complete"
for deployment in cryo-api cryo-worker cryo-governor cryo-scheduler; do
  kubectl rollout status "deployment/$deployment" -n cryo --timeout=180s
done

log "Verifying every pod actually landed on the new digest"
kubectl get pods -n cryo -o json \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
digest = '$DIGEST'.split(':', 1)[1]
mismatches = []
for p in d['items']:
    name = p['metadata']['name']
    if not any(x in name for x in ('cryo-api', 'cryo-worker', 'cryo-governor', 'cryo-scheduler')):
        continue
    # Skip pods already terminating. rollout status returns as soon as the new ReplicaSet is
    # fully available, which is before the old one's pods finish draining -- workers hold a
    # long graceful shutdown while they finish in-flight maintenance. Counting those produced
    # a false MISMATCH on the 2026-09-22 deploy; six old workers were mid-termination and
    # were gone moments later.
    if p['metadata'].get('deletionTimestamp'):
        continue
    for cs in p['status'].get('containerStatuses', []):
        image_id = cs.get('imageID', '')
        if digest not in image_id:
            mismatches.append((name, image_id))
if mismatches:
    print('MISMATCH -- these pods are not on the new digest:')
    for name, image_id in mismatches:
        print(f'  {name}: {image_id}')
    sys.exit(1)
print('All pods confirmed on the new digest.')
"

log "Deploy complete: $DIGEST is live and verified."
