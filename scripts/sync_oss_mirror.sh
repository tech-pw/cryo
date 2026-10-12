#!/usr/bin/env bash
# Mirrors this repo's main branch to the public tech-pw/cryo GitHub repo, with
# internal-only paths stripped from the *entire* history (not just the current tree) --
# git-filter-repo rewrites every commit, so a secret or internal path removed at HEAD but
# still present in an old commit would otherwise ship to the public history anyway.
#
# GitLab (this repo) is the single source of truth. GitHub is a filtered, force-pushed
# mirror -- never hand-edit tech-pw/cryo directly; a direct push there is overwritten on
# the next sync.
#
# Usage: scripts/sync_oss_mirror.sh
# Required env: OSS_SYNC_GITHUB_TOKEN (push access to tech-pw/cryo)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

GITHUB_REPO="github.com/tech-pw/cryo.git"
EXCLUDE_FILE="$REPO_ROOT/scripts/oss_mirror_exclude_paths.txt"
MAILMAP_FILE="$REPO_ROOT/scripts/oss_mirror_author_map.txt"

if [[ ! -f "$EXCLUDE_FILE" ]]; then
  echo "error: $EXCLUDE_FILE not found" >&2
  exit 1
fi

if [[ ! -f "$MAILMAP_FILE" ]]; then
  echo "error: $MAILMAP_FILE not found" >&2
  exit 1
fi

if [[ -z "${OSS_SYNC_GITHUB_TOKEN:-}" ]]; then
  echo "error: OSS_SYNC_GITHUB_TOKEN is not set" >&2
  exit 1
fi

echo "==> Cloning $REPO_ROOT into isolated workdir for history rewrite"
git clone --no-local --origin gitlab "$REPO_ROOT" "$WORK_DIR/cryo"
cd "$WORK_DIR/cryo"
git checkout -B oss-sync gitlab/main

echo "==> Stripping excluded paths and remapping known internal emails to public GitHub identities ($(wc -l < "$EXCLUDE_FILE") paths, $(wc -l < "$MAILMAP_FILE") author lines)"
git filter-repo --force --invert-paths --paths-from-file "$EXCLUDE_FILE" --mailmap "$MAILMAP_FILE" --refs oss-sync

echo "==> Scanning filtered history for leaked secrets before pushing"
if command -v gitleaks >/dev/null 2>&1; then
  if ! gitleaks detect --source . --config .gitleaks.toml --log-opts="oss-sync" --redact; then
    echo "error: gitleaks found a finding not covered by .gitleaks.toml -- aborting sync, not pushing to GitHub" >&2
    exit 1
  fi
else
  echo "warning: gitleaks not installed in this runner; skipping secret scan" >&2
fi

echo "==> Force-pushing filtered history to tech-pw/cryo main"
git remote add github "https://x-access-token:${OSS_SYNC_GITHUB_TOKEN}@${GITHUB_REPO}"
git push github oss-sync:main --force

echo "==> Done. tech-pw/cryo main now mirrors $REPO_ROOT main, minus excluded paths."
