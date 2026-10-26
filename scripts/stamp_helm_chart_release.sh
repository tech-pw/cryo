#!/usr/bin/env bash
# Stamp the Helm chart for a release before `helm package`.
#
# Usage: scripts/stamp_helm_chart_release.sh <chart-dir> <tag>
#   e.g. scripts/stamp_helm_chart_release.sh deploy/helm/cryo v0.1.6
#
# Edits <chart-dir> IN PLACE (run it on a CI checkout or a scratch copy, never commit
# the result):
#   - Chart.yaml version and appVersion = the tag without its leading "v" (v0.1.6 -> 0.1.6).
#     The chart is versioned in lockstep with the release tag, so `helm install ... --version
#     0.1.6` always installs the chart that shipped next to the v0.1.6 images.
#   - values.yaml image defaults point at the images release-supply-chain.yml pushed for the
#     same tag: ghcr.io/tech-pw/cryo (api/worker/governor/scheduler/migration/native),
#     ghcr.io/tech-pw/cryo-web and ghcr.io/tech-pw/cryo-spark.
#   - When CRYO_IMAGE_DIGEST / CRYO_WEB_IMAGE_DIGEST / CRYO_SPARK_IMAGE_DIGEST are set
#     (sha256:...), the matching digest values are stamped too, so the published chart pins
#     immutable images and passes the chart's own production digest check.
#   - The artifacthub.io/images annotation lists the same refs, and artifacthub.io/prerelease
#     is "true" for a tag with a pre-release suffix (v0.2.0-rc.1).
#
# IMAGE_PREFIX overrides the registry/namespace (default ghcr.io/tech-pw).
# Requires mikefarah yq v4 (preinstalled on GitHub-hosted Ubuntu runners).
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <chart-dir> <tag>" >&2
  exit 2
fi
chart_dir=$1
tag=$2

semver_re='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?$'
if [[ ! $tag =~ $semver_re ]]; then
  echo "error: tag '$tag' is not v<MAJOR>.<MINOR>.<PATCH>[-prerelease]" >&2
  exit 1
fi
if ! command -v yq >/dev/null || ! yq --version 2>&1 | grep -q 'mikefarah'; then
  echo "error: mikefarah yq v4 is required" >&2
  exit 1
fi
for f in Chart.yaml values.yaml; do
  [[ -f $chart_dir/$f ]] || { echo "error: $chart_dir/$f not found" >&2; exit 1; }
done

digest_re='^sha256:[0-9a-f]{64}$'
for var in CRYO_IMAGE_DIGEST CRYO_WEB_IMAGE_DIGEST CRYO_SPARK_IMAGE_DIGEST; do
  value=${!var:-}
  if [[ -n $value && ! $value =~ $digest_re ]]; then
    echo "error: $var is not a sha256 digest" >&2
    exit 1
  fi
done

prefix=${IMAGE_PREFIX:-ghcr.io/tech-pw}
prerelease=false
[[ $tag == *-* ]] && prerelease=true

export CHART_VERSION=${tag#v}
export TAG=$tag
export PRERELEASE=$prerelease
export APP_REPO=$prefix/cryo
export WEB_REPO=$prefix/cryo-web
export SPARK_REPO=$prefix/cryo-spark
export APP_DIGEST=${CRYO_IMAGE_DIGEST:-}
export WEB_DIGEST=${CRYO_WEB_IMAGE_DIGEST:-}
export SPARK_DIGEST=${CRYO_SPARK_IMAGE_DIGEST:-}

ref() { # ref <repo> <digest>
  if [[ -n $2 ]]; then printf '%s:%s@%s' "$1" "$TAG" "$2"; else printf '%s:%s' "$1" "$TAG"; fi
}
export IMAGES_ANNOTATION
IMAGES_ANNOTATION=$(cat <<YAML
- name: cryo
  image: $(ref "$APP_REPO" "$APP_DIGEST")
  platforms:
    - linux/amd64
- name: cryo-web
  image: $(ref "$WEB_REPO" "$WEB_DIGEST")
  platforms:
    - linux/amd64
- name: cryo-spark
  image: $(ref "$SPARK_REPO" "$SPARK_DIGEST")
  platforms:
    - linux/amd64
YAML
)

# yq drops blank lines; keep them so the packaged values.yaml (shown on Artifact Hub) reads
# like the committed one.
yq_in_place() { # yq_in_place <expression> <file>
  local tmp
  tmp=$(mktemp)
  sed 's/^$/#__CRYO_BLANK__/' "$2" | yq "$1" - | sed 's/^#__CRYO_BLANK__$//' >"$tmp"
  cat "$tmp" >"$2"
  rm -f "$tmp"
}

yq_in_place '
  .version = strenv(CHART_VERSION) |
  .appVersion = strenv(CHART_VERSION) |
  .annotations["artifacthub.io/images"] = strenv(IMAGES_ANNOTATION) + "\n" |
  .annotations["artifacthub.io/prerelease"] = strenv(PRERELEASE)
' "$chart_dir/Chart.yaml"

yq_in_place '
  .image.repository = strenv(APP_REPO) | .image.tag = strenv(TAG) | .image.digest = strenv(APP_DIGEST) |
  .migration.image.repository = strenv(APP_REPO) | .migration.image.tag = strenv(TAG) | .migration.image.digest = strenv(APP_DIGEST) |
  .native.image.repository = strenv(APP_REPO) | .native.image.tag = strenv(TAG) | .native.image.digest = strenv(APP_DIGEST) |
  .web.image.repository = strenv(WEB_REPO) | .web.image.tag = strenv(TAG) | .web.image.digest = strenv(WEB_DIGEST) |
  .spark.k8s.image.repository = strenv(SPARK_REPO) | .spark.k8s.image.tag = strenv(TAG) | .spark.k8s.image.digest = strenv(SPARK_DIGEST)
' "$chart_dir/values.yaml"

echo "Stamped $chart_dir as chart $CHART_VERSION for images tagged $TAG (prerelease=$PRERELEASE)"
