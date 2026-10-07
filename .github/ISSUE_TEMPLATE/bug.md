---
name: Bug report
about: Report something that is broken or behaves incorrectly
title: "bug: "
labels: bug
assignees: ""
---

<!-- Thanks for taking the time to file a clear bug report. The fields below help us
     reproduce and fix the issue without a back-and-forth. -->

### What happened

<!-- A short, factual description. One paragraph is fine. -->

### Reproduction steps

1.
2.
3.

### Expected behavior

<!-- What you expected to happen instead. -->

### Environment

- Cryo version (tag or commit SHA):
- Deployment mode (local docker compose / Helm / pip install):
- Catalog type (rest / hive / glue / nessie / polaris / other):
- Storage (s3 / gs / abfs / hdfs / file) and warehouse URI (no credentials):
- Engine (trino / spark_emr / native / flink) and version:
- Scrubbed source/destination config (redact keys, tokens, passwords):
- Python version (if backend-related):
- Browser (if UI-related):

### Logs / screenshots

<!-- Paste the relevant output, or link to a gist. Scrub any secrets first. -->

### Severity

<!-- One of: blocker (data loss / outage), high (broken feature), medium (wrong but
     recoverable), low (cosmetic / minor). Pick what best describes the impact on your
     operation. -->