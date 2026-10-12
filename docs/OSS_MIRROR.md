# Public GitHub mirror

GitLab `main` is the source of truth. [tech-pw/cryo](https://github.com/tech-pw/cryo) is a
filtered copy that `scripts/sync_oss_mirror.sh` rebuilds and force-pushes. The `oss-mirror-sync`
job in `.gitlab-ci.yml` runs it on a schedule or by hand. Never commit to GitHub directly: the
next sync overwrites it.

## What the sync changes

The script clones GitLab `main` and runs `git filter-repo` over the whole history:

| Input | Effect |
|---|---|
| `scripts/oss_mirror_exclude_paths.txt` | Removes these paths from every commit. Lines may use `glob:` or `regex:`; `#` starts a comment. |
| `scripts/oss_mirror_replacements.txt` | Rewrites matching text in every file and every commit message (internal hostnames, buckets, IPs, Slack IDs, company names). |
| `scripts/oss_mirror_author_map.txt` | Maps work emails to the public GitHub identities of each author. |

Then it runs gitleaks with `.gitleaks.toml` over the filtered history and refuses to push on a
finding.

The replacements file has **no comment syntax**. filter-repo treats every non-blank line as a
pattern, so a `#` line would redact that literal text everywhere. Keep every rule a `regex:` line;
regex rules run in file order, after any literal rules. The replacements file and the author map
are themselves excluded from the mirror, since they name the hosts and emails they hide.

## Adding a new internal identifier

1. Remove it from the current tree in GitLab. The rewrite is a safety net for history, not a
   substitute for keeping the source clean.
2. Add a `regex:` rule to `scripts/oss_mirror_replacements.txt`, or an exclude path for a file
   that should never ship.
3. Run the filter on a scratch clone and grep the rewritten branch (`git log oss-sync -p`) before
   the next sync.

Every rule change rewrites mirror history from the first affected commit, so commit SHAs on
GitHub change. Anyone with a clone of the mirror has to re-clone or hard-reset to the new `main`.

## History

- 2026-10-12: first content scrub. Production hostnames, S3 bucket names, VPC IPs, Slack IDs,
  Confluence and Jira links, and internal working docs (cost logs, incident write-ups, Jira
  plans) were removed from the whole mirror history and GitHub `main` was force-pushed once.
