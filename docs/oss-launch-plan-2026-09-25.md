# Cryo open-source launch and adoption plan

Date: 2026-09-25. Status: **proposed delivery plan; implementation gates remain open**.
Audit baseline: `231c799d4e0899f5c1d0e9ce9bcec059cd5a5740`.

## 1. Decision and intended outcome

Make Cryo a fully open, self-hosted Iceberg maintenance and health platform that an
organization can install with Helm, connect to its existing catalog and execution engine,
and use to reduce the **net cost** of operating its lakehouse. Keep the existing Apache-2.0
license and make the complete core, UI, chart, connector contracts, and build instructions
available without a license server or mandatory hosted service.

**Recommendation: proceed with a focused portability and release program. The current
chart is not yet ready to promise an independent organization a working Helm installation.**
Generic source/engine adapters already exist; this is not a rewrite. More research algorithms
are not on the critical path. Installation, safe execution, understandable outcomes, and
maintainer responsiveness will determine early adoption.

This document owns portability, distribution, and adoption sequencing. It replaces the July
[OSS readiness plan](OSS_READINESS.md). The root architecture contracts remain authoritative;
[cost/reliability work](CRYO_COST_REDUCTION_PLAN.md) and unresolved recovery gates remain
prerequisites for production claims. A public source release does not close those gates.

### What "simple Helm install" means

Helm installs a working platform with documented dependencies and secure access. The operator
still supplies catalog/engine endpoints, credentials or workload identities, network access,
and an explicit retention policy. Cryo must not need PW networking, private artifacts,
Karpenter, Grafana, Slack, an AI provider, or a particular catalog/schema name to work.

The intended journey is:

1. Choose evaluation or production values; install a pinned release from a public registry.
2. Authenticate and add a **Source** (catalog), then a separate **Destination** (engine).
3. Run connection/permission diagnostics and choose a limited namespace/table scope.
4. Scan in read-only mode and see health findings without query logs or billing integrations.
5. Preview a plan with its scope, operations, retention constraints, and estimated cost.
6. Explicitly enable maintenance for a disposable/example table, then a small real cohort.
7. Verify outcomes and configure a bounded schedule; expand only when evidence supports it.

Proposed command shape below; the OCI location and complete artifact set are **not yet
published**. This is a delivery target, not today's quickstart:

```sh
helm upgrade --install cryo oci://ghcr.io/shelf-project/charts/cryo \
  --namespace cryo --create-namespace \
  --version <chart-version> --values cryo-values.yaml \
  --wait --wait-for-jobs --timeout 10m
```

The public guide must explain every required value and secret before this command. Secrets
must not be pasted into command arguments or committed values files. Initial access can use
port-forwarding; ingress/TLS is a documented production option.

## 2. Findings that determine the work

These are observations from current files, not an exhaustive security or production audit.
`helm lint` passed, and a chart render with release `adoption-check` in namespace
`iceberg-platform` completed. Neither proves a working installation. Full CI and a live
fresh-cluster installation were not run for this planning audit.

| Area | Verified state | Required response |
|---|---|---|
| Portable foundation | [Catalog factory](../cryo/sources/factory.py) supports REST, Hive, Glue, Polaris and Nessie; catalog properties pass through. Separate sources/destinations, connection checks, and IAM/default-chain/cross-account options already exist. | Retain and validate these paths. Factory dispatch is not proof that every catalog/auth/storage combination works. |
| Default installation | [Chart values](../deploy/helm/cryo/values.yaml) use local images, disable the UI, and use development auth. [Chart.yaml](../deploy/helm/cryo/Chart.yaml) has no Postgres dependency; bundled Redis is not automatically connected through application configuration. | Ship complete public artifacts and explicit dependency/auth wiring. |
| Names and placement | [Web proxy](../web/nginx.conf) assumes `cryo-api:8000`. [Chart helpers](../deploy/helm/cryo/templates/_helpers.tpl) pin app pods to amd64; [Spark K8s config](../cryo/engines/spark/k8s.py) and chart defaults require a Karpenter label. | Derive service addresses from Helm settings; make placement configurable without provider-specific defaults. |
| Scheduling | [Admission](../cryo/governor/admission.py), configuration, report modules and destination UI hardwire IST. | Add explicit IANA timezone configuration and a compatibility migration; preserve existing installations' behavior. |
| Optional integrations | [EMR](../cryo/engines/spark/emr.py) defaults include placeholder artifact/warehouse paths and a fixed Glue catalog name. [Actual-cost reporting](../cryo/reporting/cost_ceiling.py) embeds a datasource UID and CUR table. | Require valid EMR configuration before enabling it. Make cost adapters configurable and optional; this cost feature is already disabled by default. |
| Enterprise connections | [Trino client](../cryo/engines/trino/client.py) constructs basic or unauthenticated connections and has no explicit custom CA option. [Catalog adapter](../cryo/sources/pyiceberg_catalog.py) needs full nested-namespace round-trip validation. | Publish narrow, verified auth support; add trust-bundle configuration and preserve complete table identifiers. |
| Storage claims | [Orphan scanner](../cryo/scanner/orphans.py) skips non-S3 locations; pricing assumptions are AWS-oriented. | Verify S3/S3-compatible profiles first; report unsupported functions and unknown prices explicitly. |
| Releases and builds | [Release workflow](../.github/workflows/release-supply-chain.yml) already signs/attests and creates SBOMs, but publishes only the backend image. Evidence is retained as expiring workflow artifacts. [Dockerfile](../Dockerfile) copies the whole build context; [.dockerignore](../.dockerignore) does not exclude all local/private material. | Publish the complete coherent artifact set, retain durable evidence, and audit clean build contexts/final image contents. This finding does not establish an existing image leak. |
| First-user tests | [Nightly](../.github/workflows/e2e-nightly.yml) references absent `tests/test_health.py` and permits browser smoke failure. Chart tests mostly render configurations rather than install on Kubernetes. | Make the documented adopter journey a required release check. |
| OSS process | License, contributing/security/conduct policies, governance, support policy, capability generation and CI already exist. Security issue template invites public reproduction details; ownership and policies need reconciliation. | Fix the disclosure route, name maintainers, verify repository settings, and rehearse contribution/support flows. |
| Public claims | [README](../README.md) has stale native-engine/bandit wording and "exact" savings language. Generated [capabilities](CAPABILITIES.md) include GA labels backed partly by factory/golden tests. | Align claims with execution and operational evidence, using the [paper audit](PAPER_IMPLEMENTATION_AUDIT_2026-09-24.md). Update the canonical capability manifest, not its generated Markdown by hand. |

At audit time GitHub reported the repository as private and no published GitHub releases.
Recent nightly failures are on an older remote revision (`a8915bd6`); they are not evidence
that this local revision fails. Every release gate must be rerun at the actual candidate commit.

## 3. Product boundaries for the first release

### Installation profiles

| Profile | Contract |
|---|---|
| Evaluation | UI enabled, minimal tested resource footprint, optional bundled Postgres/Redis, generated or supplied persistent secrets, safe initial access, maintenance disabled until explicitly configured. Demo catalog/Trino/storage remain a separate optional fixture. |
| Production | External Postgres/Redis supported; existing secrets and workload identity supported; TLS/auth, backups, resource sizing and network requirements documented. No dependency on a particular cloud's node labels, ingress controller or autoscaler. |
| Optional execution | Spark on Kubernetes and native execution enabled only when selected, with required images/roles/capabilities checked. EMR and other conditional paths carry their own prerequisites and evidence. |

Keep Postgres as the durable source of truth. The current Redis design is intentionally
nonpersistent with recovery from Postgres; verify that recovery before recommending it, rather
than adding persistence without a recovery design. Document the initial schema/`pgcrypto`
permission requirements; keep optional extensions optional.

Public defaults must fit a small evaluation. Current static application requests/replica
floors total roughly 3.45 CPU and 6.125 GiB before scheduled scan jobs; this is a manifest
estimate, not measured usage. Benchmark small/medium/large profiles and show idle and active
footprint. Avoid installing PW-sized capacity for every adopter.

### Compatibility and maintenance scope

Use **REST catalog + S3-compatible storage + Trino** as the first independent reference
environment. Validate **Spark on Kubernetes** as a separate execution profile. Existing
Hive/Glue/Polaris/Nessie adapters remain available, with support labels derived from recorded
integration results. Do not advertise universal cloud, catalog, authentication, table-format
or engine compatibility just because a library accepts a configuration.

The published support matrix must identify exact tested Kubernetes/Helm, Postgres/Redis,
catalog/auth, storage, Trino, Spark and Iceberg runtime versions, plus table-format/feature
boundaries. Cover supported partition transforms, schema/spec evolution, delete files,
branches/tags, nested identifiers and concurrent writers. Native remains conditional on its
existing routing/fallback capabilities; Flink configuration generation must not imply execution.

Core launch value: discovery, health, dry-run/preview, bounded optimization, manifest rewrite,
snapshot expiry, orphan cleanup where supported, audit, and honest impact reporting. Query
telemetry enriches prioritization; it must not be required for the first useful scan or run.

Preserve load admission, partition-aware chunking, cooldowns, locks, failure isolation, and
the canonical operation order. Keep all SQL in engine renderers. Changing timezone contracts
requires matching `lld.md`, migration, UI/API, and parity-test updates.

## 4. Delivery workstreams and acceptance criteria

Owners below are roles to assign before implementation, not assumed named commitments.
Effort ranges are initial engineer-day estimates, excluding pilot waiting time and unexpected
correctness defects. Workstreams can overlap after OSS-0.

### OSS-0 — Fix the release boundary and evidence baseline

**Owner:** release lead + backend maintainer. **Estimate:** 2–3 days.

- Record the candidate commit, open recovery/safety gates, current CI results, supported
  combinations, and an explicit implementation-versus-production evidence distinction.
- Identify public project source versus organization-specific deployment/configuration.
  Move private overlays, internal runbooks, endpoint inventories, billing extracts and real
  customer fixtures into an operator-owned private location. Keep every core capability open.
- Inventory tracked files, full history, release archives, docs, generated assets and image
  contents for secrets/internal data. Scan clean source and all distributed artifacts. Any
  discovered credential requires rotation as well as removal; history handling is a deliberate
  maintainer action, never an incidental rewrite in a packaging PR.
- Record release rights and third-party provenance, including the patched Iceberg Go copy and
  distributed JARs. Preserve applicable license/NOTICE material in source and images.
- Replace the public security issue form with a private reporting link and test its routing.

**Done when:** one owned checklist names every public artifact and current blocker; no
unresolved sensitive-material exposure is included in the release candidate; source and
artifact attribution is reviewed; the private reporting path works.

### OSS-1 — Remove runtime organization assumptions

**Owner:** backend + connectors maintainers. **Estimate:** 5–8 days.

- Build an inventory that distinguishes executable assumptions, configurable defaults,
  historical incident comments, and sensitive publication material. A string scrub alone is
  not a portability fix.
- Default Spark placement to no cloud-specific selector. Make node selectors, affinity,
  tolerations, identities and registry locations configurable. Preserve PW behavior in its
  explicit private values overlay.
- Add an organization/destination IANA timezone. New installations default to UTC; migrate
  existing implicit IST settings to explicit `Asia/Kolkata`. Define precedence and DST policy.
  Test UTC, Kolkata, DST transitions, midnight-crossing and disabled off-peak windows.
- Preserve complete nested table identity through discovery, persistence, bindings, preview
  and execution; test duplicate leaf names, quoted names and nested namespaces.
- Add custom CA trust support and connection diagnostics without weakening TLS verification.
  Validate catalog/engine/storage identity separately. Publish exact supported auth methods.
- Replace EMR placeholders with required validated inputs or disable that execution mode
  with an actionable reason. Extract cost datasource/table/namespace settings into the optional
  cost adapter; support absent/stale billing data without blocking maintenance.
- Keep metadata bounds and query-log contents under the same redaction/access rules as other
  potentially sensitive customer data. External telemetry and AI remain explicitly opt-in.

**Done when:** the reference stack operates without PW accounts, names, labels, network access,
query-log tables or billing services; the existing deployment retains its explicit settings
and passes behavior/parity tests. Unsupported combinations fail before scheduling work.

### OSS-2 — Deliver a complete, secure Helm release

**Owner:** platform/release maintainer. **Estimate:** 6–10 days.

- Publish versioned backend, web and required optional execution images plus an OCI chart;
  lock their compatible versions/digests together. Verify anonymous pull. Support amd64 and
  arm64 only when the corresponding builds and execution tests pass.
- Add chart metadata, `values.schema.json`, documented values, useful `NOTES.txt`, and
  configuration validation. Derive service names/ports and dependency connection settings.
  Test custom release name, namespace, port and two releases sharing a cluster.
- Implement the evaluation/production profiles above. Support existing Secret references,
  common/per-component ServiceAccount configuration and annotations, and workload identity.
  Give optional job runners only the required namespaced permissions, including cleanup.
- Default to secure authentication. Reuse supported OIDC/JWT mechanisms and provide a
  documented bootstrap alternative for organizations without an IdP. Development-token
  issuance must not be externally exposed by default. Keep signing/encryption keys stable
  across upgrades, protect them in backups, and document recovery and rotation.
- Add tested non-root/security contexts, configurable network policies, private CA mounting,
  ingress/TLS options, probes and disruption controls where meaningful. Core install must not
  require cluster-admin privileges, KEDA, Karpenter, or unrelated CRDs.
- Design migration ordering for both bundled and external Postgres. The current pre-install
  hook cannot simply be kept ahead of a newly added normal-resource database/secret. Ensure a
  single migration owner, readiness/locking, bounded retry and understandable failure output.
- Build from a clean checkout with explicit copy/ignore rules. Document registry mirrors and
  engine artifact requirements; verify any claimed offline profile has no runtime downloads.

**Done when:** required cluster CI installs both documented profiles, reaches the UI, restarts
cleanly, preserves secrets, and passes a previous-version upgrade. A failed migration prevents
incompatible application startup and leaves a documented recovery path.

Follow [Helm chart conventions](https://helm.sh/docs/chart_best_practices/) and distribute
versioned charts through [OCI](https://helm.sh/docs/topics/registries/). Test the declared
security profile against [Kubernetes Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/).

### OSS-3 — Make first success and troubleshooting self-service

**Owner:** frontend + backend + documentation maintainers. **Estimate:** 4–7 days.

- Extend the existing Connections flow: Source → Destination → binding → diagnostics → scope
  → first scan → plan preview → explicit maintenance opt-in. Keep sources and engines separate.
- Explain missing privileges, unreachable endpoints, unsupported operations, stale scans,
  retention conflicts and absent optional telemetry with a specific remedy. Never turn a
  missing integration into a misleading zero-cost or zero-savings result.
- Provide least-privilege examples for catalog read, engine execution, metadata/object access
  and deletion, separating identities where possible. Initial scan/preview must not need
  destructive permissions.
- Add a guided sample dataset, screenshots and a short public install/upgrade/backup/uninstall
  guide. Explain which Cryo resources uninstall removes, PVC/secret retention, and that it must
  not delete the adopter's warehouse or externally managed services.
- Supply redacted diagnostics/support bundles that users inspect before sharing; include
  versions, configuration status, run IDs and actionable errors, never credentials or raw
  query/data contents by default.

**Done when:** two people unfamiliar with the code complete an installation using only public
instructions. Initial usability targets: UI reachable within 10 minutes of satisfied
prerequisites; first scoped scan within 30 minutes. Record hardware, table count and actual
timings; these are targets, not performance claims for arbitrary fleet sizes.

### OSS-4 — Prove safe execution, upgrades and net value

**Owner:** reliability/test + engine maintainers. **Estimate:** 6–10 days plus observation.

- Repair nightly test targets and make install/browser/maintenance smoke checks required.
  Test actual disposable catalog, storage and engine services as well as unit/parity/golden
  contracts. Required checks must pass at the same commit used for release artifacts.
- Run install → authenticate → connect → scan → preview → execute → verify → upgrade. Verify
  row contents/schema and logical delete semantics as well as file counts, audit and status.
- Test writer conflicts, process interruption, Redis restart, lock expiry, queue recovery,
  partial operation failures, missing metadata, and retry idempotence. Reuse the existing
  recovery plans and deployed conformance framework; do not replace them with mock-only proof.
- Exercise destructive cleanup with retention requirements, branches/tags, in-flight writes,
  shared locations and object-store versioning. Report physical reclamation separately from
  metadata changes; check that required live data and retained snapshots remain readable.
- Rehearse fresh DB creation, previous-release upgrade, failed migration, backup restore and
  compatible application rollback on representative persisted state. Preserve migration
  history. Helm rollback does not undo DB changes or Iceberg maintenance.
- Publish a reproducible workload and matched before/after/control evidence with raw failures,
  engine versions, workload shape, query latency/CPU, object/file effects, retention delay and
  Cryo overhead. Follow [cost proof requirements](CRYO_COST_REDUCTION_PLAN.md) and
  [cost tracking](cost-tracking/README.md); separate synthetic results from production evidence.

Use this accounting boundary:

```text
net benefit = avoided query + storage + request costs
              - maintenance compute/requests
              - Cryo infrastructure
              - retries and temporary retained storage
```

Show estimated opportunity, observed technical effects, and realized billing changes
separately. Less Trino CPU is not automatically a lower bill without changed paid capacity.
Missing pricing produces "unavailable" or an explicit estimate. Demonstrate when maintenance
is not worth running. Do not promise a universal savings percentage or that every install
will have positive savings.

**Done when:** every advertised write profile passes its correctness/recovery tests; the
supported upgrade path has deployed evidence; reproducible net-value results and limitations
are public. Production-support status additionally requires the relevant live/pilot evidence.

### OSS-5 — Make releases and contribution sustainable

**Owner:** release lead + project maintainers. **Estimate:** 3–5 days, alongside other work.

- Retain existing OSS policies; fix stale links, support-version conflicts and README claims.
  Add named maintainers/CODEOWNERS, a private conduct-report route with an alternative contact,
  a lightweight RFC/decision process, and a documented contribution-provenance policy.
- Use a single version/release process for source, chart, images and notes. Attach durable
  checksums, notices, SBOMs, provenance/signature instructions, support matrix, upgrade evidence
  and limitations to each release; expiring CI artifacts are insufficient as the only copy.
- Protect release branches/tags and critical paths, minimize workflow permissions, pin actions
  to reviewed revisions, and cover Python, JS, Go, images and distributed engine artifacts in
  dependency maintenance. Verify repository settings rather than assuming YAML enables them.
- Provide a Linux clean-checkout contributor path and real small issues. Rehearse one external
  documentation/code contribution without internal credentials. Open a public discussion
  channel with named triage ownership and realistic response expectations.
- Publish a concise project page/README: who Cryo serves, supported stack, install, first result,
  measured examples with caveats, architecture, support boundaries and contribution link.
  Publish the paper-to-implementation audit without overstating research fidelity.

**Done when:** an unauthenticated adopter can obtain and verify every required artifact, report
a vulnerability privately, follow a working contributor guide, and identify who maintains the
release and how long it is supported.

Use a versioned [OpenSSF OSPS Baseline](https://baseline.openssf.org/) checklist proportionate
to project maturity, with owners and evidence; do not claim certification. Apply GitHub's
[secure Actions guidance](https://docs.github.com/en/actions/reference/security/secure-use)
and teach consumers how to verify [artifact attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations).
An [OpenSSF Best Practices](https://www.bestpractices.dev/en/criteria/0) self-assessment can
follow; badges and foundation membership are not launch prerequisites.

## 5. Sequence and release gates

Indicative schedule with two engineers and part-time maintainer/test support: **4–6 weeks to
an independently installable public alpha**, subject to baseline findings. Beta/production
timing depends on pilot and correctness evidence, not a calendar promise. Re-estimate after
OSS-0; the workstream estimates total 26–43 engineer-days before external observation.

| Stage | Work and dependency | Exit gate |
|---|---|---|
| Baseline | OSS-0; start policy/claims cleanup in OSS-5 | Candidate scope, owners, publication inventory and actual CI blockers recorded. |
| Portable installation | OSS-1 and OSS-2 in parallel; OSS-3 develops against agreed config/auth contracts | Independent cluster gets a working authenticated platform and scoped read-only scan. |
| Public alpha | Complete publication hygiene and release tooling; OSS-4 validates the profiles exposed in alpha | Public source/chart/images/docs, required install CI, safe defaults, explicit experimental boundaries. Unsupported write paths remain disabled/labeled; no blanket production claim. |
| External beta | Two non-PW organizations or genuinely independent environments, including vanilla Kubernetes without AWS/Karpenter assumptions | Operators install without code edits or maintainer shell access, complete verified maintenance, and recover an induced failure from public docs. |
| Production-supported release | Close applicable recovery/conformance/cost and upgrade gates | Defined supported combinations, sustained operational evidence, documented restore path, measured net-value case studies, named patch/triage ownership. |

An alpha can ship with a smaller verified write surface, or read-only if write safety is not
yet established. It must clearly state that scope. Calling the source public must not imply
all catalog/engine combinations or old "GA" labels have been externally validated.

### First implementation batch

1. **PR 1 — baseline and publication hygiene:** actual candidate CI, private/public inventory,
   clean build-context rules, disclosure routing, corrected claims. Keep history handling and
   any credential rotation separate from routine edits.
2. **PR 2 — portable chart foundation:** public image coordinates, web service naming,
   dependency/config schema, secure secrets/bootstrap and migration ordering.
3. **PR 3 — runtime portability:** timezone compatibility, configurable placement and optional
   integration validation; split nested-identifier/auth changes into focused follow-ups.
4. **PR 4 — required adopter integration CI:** clean-cluster Helm install and first-use journey,
   then upgrade/recovery and advertised write-profile tests.
5. **PR 5 — public installation and contributor package:** versioned artifacts/evidence,
   documentation rehearsal, support matrix and pilot instructions.

These are work packages, not a demand to combine unrelated changes into five large PRs.
Each implementation change updates `todo.md`, relevant contracts, tests and operator docs.
The act of changing repository visibility or publishing a release is a later release decision;
this planning change performs neither.

## 6. Adoption scorecard and explicit non-goals

Track installation completion, time to first scan, time to first verified maintenance,
actionable failure rate, independent active installations where voluntarily reported,
upgrade success, issue response time, and contributions from outside the founding team.
For value, track net cost with evidence coverage and query/reliability guardrails, not GitHub
stars or file-count reductions alone. Collect pilot observations manually first; any product
telemetry must be opt-in, documented, and unnecessary for operation.

Not required for this launch: another planner/AI algorithm, complete PTO training, universal
cloud/auth support, arbitrary dynamic plugin loading, a managed SaaS, multi-tenant hosting,
foundation membership, or a large governance organization. Use the existing versioned
[connector contract](ECOSYSTEM_CONNECTORS.md) when expanding integrations.

**Definition of success:** a team outside PW installs a pinned public release, safely maintains
its own supported Iceberg tables, understands whether the measured benefit exceeds the cost,
and can operate, upgrade and contribute using public resources alone.
