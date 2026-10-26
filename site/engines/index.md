---
title: Engines
description: Which maintenance operations Cryo renders for Trino and Spark, how plans become SQL, and where native compaction fits.
canonical: https://tech-pw.github.io/cryo/engines/
updated: 2026-10-16
---

# Engines

The planner is engine-agnostic. Trino SQL and Spark SQL are rendered apart, so the same plan produces different SQL and never different intent.

## Rendered operations

Every operation in the canonical order has a renderer for each engine, with one exception: `rewrite_position_deletes` is Spark only today.

| Operation | Trino | Spark | Notes |
| --- | --- | --- | --- |
| `convert_deletes` | As OPTIMIZE | Yes | Spark passes a delete-file threshold. Trino runs a plain `OPTIMIZE`. |
| `rewrite_position_deletes` | Not supported | Yes | Spark only. |
| `optimize` | Yes | Yes | Trino `EXECUTE optimize` is Spark `rewrite_data_files`. |
| `compute_statistics` | Yes | Yes | Runs right after `optimize`. |
| `optimize_manifests` | Yes | Yes | Trino `optimize_manifests` is Spark `rewrite_manifests`. |
| `expire_snapshots` | Yes | Yes | Guarded. Needs the relevant scanner tier. |
| `remove_orphan_files` | Yes | Yes | Guarded. Needs the relevant scanner tier. |

## Plan to renderers

The plan carries operations and partition predicates, never a column type or a SQL keyword. Raw partition values from the scan, such as epoch-day integers for day transforms, are converted to predicates inside the renderer.

Figure 1One plan, two dialects: only the renderers write SQL

*One plan, two renderers. Neither dialect leaks into the planner.*

- **Write or execute
Text description
- Planner to Trino renderer
- Planner to Spark renderer
- Trino renderer to EXECUTE optimize (render)
- Spark renderer to rewrite_data_files (render)

This split keeps changes local. A planner change, such as operation order or predicate logic, reaches both engines. A renderer change reaches only its own dialect. Each renderer has golden tests that pin the exact SQL for a given plan.

## The same operation, side by side

A compaction of one table, as each engine receives it.

**Trino**

```
ALTER TABLE prod.orders_daily
  EXECUTE optimize
  WHERE ts >= TIMESTAMP '2026-09-01'
```

**Spark**

```
CALL prod.system.rewrite_data_files(
  table => 'prod.orders_daily',
  strategy => 'sort',
  sort_order => 'ts ASC',
  options => map('target-file-size-bytes', '536870912')
)
```

Trino file sizing

On Trino the target file size is set on the Iceberg catalog with `iceberg.target-max-file-size`. Before an `OPTIMIZE`, Cryo only tunes session properties Trino supports: `task_min_writer_count` and `writer_scaling_min_data_processed`.

## Native compaction

Native Go compaction is a configured engine path and is not assumed to be on. The planner does not change shape for it. It receives the same plan as Trino and Spark, and runs the rewrite itself. Flink is not on this page. Cryo can emit a Flink maintenance config and does not submit the job.

## Cryo engines and your engines

Engines live under **Settings → Connections → Engines**, split into two groups by who runs them.

cryo.local/settings/connections?tab=engines

*Cryo engines run inside the deployment. Your engines are clusters you connected.*

**Cryo engines**

Shipped with Cryo and run inside the deployment: the native engine (a subprocess on Cryo's workers) and Spark on Kubernetes (an engine with `submit_mode: k8s`). Migrations seed them.

**Your engines**

Trino, EMR Serverless or Flink clusters you added with **New engine**. Presets and manual runs can target any of them.

Cryo engines stay editable, because operators tune their slot caps and timeouts. Other work depends on them, so **Edit** and **Disable** ask for confirmation first and say what changes:

| Engine | What the confirmation warns about |
| --- | --- |
| Native | The slot cap and timeouts bound every job routed to it. Presets that target it pick up the change on their next run. |
| Spark on Kubernetes | The nightly batch needs exactly one enabled Spark engine. Disabling this one, or enabling a second, fails the batch until that is fixed. |

Edits to a Cryo engine persist across upgrades. Deleting one is permanent: Cryo does not re-create it, so add it back by hand if you need it. Deleting any engine fails while a preset or run still references it. To stop using an engine without losing it, disable it.

The **Default preset** badge marks the engine the enabled default preset targets. It can be in either group.

## Where to go next

MaintenanceThe plan these renderers execute, and the order behind it.ArchitectureWhere the planner and workers sit relative to Trino and Spark.DeploymentEngines stay outside the Helm chart. See how they connect.ReferenceFeature presence across engines and adjacent tools.

Source: https://tech-pw.github.io/cryo/engines/
Markdown: https://tech-pw.github.io/cryo/engines/index.md
