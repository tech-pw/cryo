# Support policy

Cryo is self-hosted software. Operators own their infrastructure, credentials,
catalogs, engines, backups, and incident response. The project provides
release artifacts, documentation, and community triage; it does not currently
offer a commercial availability or response-time SLA.

## Supported releases

Until Cryo reaches 1.0, only the latest tagged release is supported. After 1.0,
the project intends to support the latest minor release and its immediate
predecessor for 90 days after a newer minor release. This intended 1.0 policy
does not begin until it is confirmed in release notes.

Support means:

- security and correctness fixes are targeted to a supported release;
- upgrade instructions describe the supported path from the previous release;
- capability and known-limitations records match the shipped release;
- release artifacts include provenance, SBOM, migration-render, and evidence
  records.

Development branches, untagged images, local modifications, and unsupported
catalog/engine combinations are best-effort only.

## Where to ask

| Need | Channel |
|---|---|
| Reproducible defect | GitHub Issue |
| Usage or design question | GitHub Discussion |
| Security vulnerability | Private GitHub Security Advisory |
| Feature request | GitHub Issue with workload and impact evidence |

Never post credentials, connection strings, SQL containing sensitive values,
table data, or private metadata in public channels. Use the privacy-safe
Maintenance Decision Record when sharing decision evidence.

## Incident classification

These labels guide triage; they are not response-time guarantees.

| Severity | Definition | Examples |
|---|---|---|
| Stop-ship | Credible data loss/corruption, authorization bypass, secret exposure, or destructive scope escape | Incorrect orphan deletion, cross-tenant action, leaked destination secret |
| Critical | Production control plane unavailable or unsafe with no practical workaround | Fencing failure, recovery loop repeatedly resubmits an unknown effect |
| Major | Material function broken with a safe workaround | One engine renderer fails while a compatible destination remains available |
| Normal | Limited defect, documentation issue, or enhancement | Incorrect non-safety metric, missing adapter |

For stop-ship reports, disable affected automation, engage the global hold,
preserve audit/effect evidence, and follow the recovery runbooks. Do not retry
an unknown external effect without reconciliation.

## Required diagnostic bundle

Provide the tagged version and image digest, deployment mode, capability
manifest version, correlation ID, redacted timestamps, affected operation and
engine, relevant audit/effect identifiers, and minimal reproduction. Do not
attach raw secrets, full query text, partition values, or table data.

## Compatibility boundary

Only combinations marked supported in `docs/CAPABILITIES.md` are covered.
Preview, shadow-only, fallback-required, and unsupported cells retain those
limitations regardless of whether code paths exist. PTO and WAIR remain
non-executing until their evidence gates pass.
