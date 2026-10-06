---
title: Engines
description: Which maintenance operations Cryo renders for Trino and Spark, how plans become SQL, and where native compaction fits.
canonical: https://tech-pw.github.io/cryo/engines/
updated: 2026-10-05
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

Native Go compaction is a configured engine path and is not assumed to be on. The planner does not change shape for it. It receives the same plan as Trino and Spark, and runs the rewrite itself.

## Where to go next

MaintenanceThe plan these renderers execute, and the order behind it.ArchitectureWhere the planner and workers sit relative to Trino and Spark.DeploymentEngines stay outside the Helm chart. See how they connect.ReferenceFeature presence across engines and adjacent tools.

Source: https://tech-pw.github.io/cryo/engines/
Markdown: https://tech-pw.github.io/cryo/engines/index.md
