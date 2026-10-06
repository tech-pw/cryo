---
title: Architecture
description: Cryo's place in the lakehouse, system context, the scheduled maintenance sequence, the job lifecycle, assignment, and the job of each component.
canonical: https://tech-pw.github.io/cryo/architecture/
updated: 2026-10-06
---

# Architecture

Cryo sits between Apache Iceberg catalogs on the read side and engines on the write side. Sources never reach destinations without going through the plan.

## Where Cryo sits

Cryo is a maintenance control plane for the lakehouse you already run. It does not replace the catalog, Trino, Spark or object storage. Operators, catalogs and engines sit on the left; Cryo is the middle; the lake stays on the right.

Figure 1Cryo in the lakehouse

*Select a Cryo service to open the page that explains it. Cryo is not a catalog: it reads metadata, plans work, and admits it to engines you already operate.*

Text description
- Operators use the Web UI. Catalogs, destinations and monitors sit beside them and point into the stack Cryo talks to.

- The stack you already run is Iceberg REST, Apache Hive, AWS Glue, Trino and Spark on EMR.

- Cryo’s control plane is the HTTP API, Scanner, Planner, Governor, Scheduler and Workers. The planner emits no SQL.

- PostgreSQL, Redis, Kubernetes and Helm sit under Cryo as stores and deploy.

- The data lake is Amazon S3 holding Iceberg data files. Engines write there. The scanner does not read those files.

## System context

The scanner only reads sources. Workers only execute on destinations. PostgreSQL holds durable state, Redis holds the queue and the locks, and object storage holds the table data.

Figure 2Cryo reads your catalogs, then maintains tables through your engines

*Select a node to open the page that explains it. Hover a node to follow its connections.*

- **Read path

- **Write or execute

- **Control or probe
Text description
- Step 1: Iceberg REST to Scanner (metadata)

- Step 2: Scanner to PostgreSQL (stats)

- Step 3: Scheduler to Redis (queue)

- Step 4: Governor to Workers (admit)

- Step 5: Workers to Planner (plan)

- Step 6: Workers to Destinations (execute)

- Step 7: Trino to Object storage (write files)

- Hive or Glue to Scanner

- Spark on EMR to Object storage

- Web UI to API (REST)

- API to PostgreSQL (reads)

- Governor to Redis (slots)

- Governor to Destinations (probe live load)

## Scheduled maintenance

The scheduler enqueues work for presets that are due. The governor probes live engine load and admits jobs. A worker asks the planner for a plan, the engine executes it, and the result is written to the audit log.

Figure 3Follow one scheduled job: enqueue, admit, execute

*One scheduled job, from enqueue to audit.*

- **Write or execute

- **Read path

- **Control or probe
Text description
- Scheduler to PostgreSQL: find due presets and resolve tables from rules and assignments

- Scheduler to PostgreSQL: create the run and its jobs as queued

- Scheduler to Redis: enqueue jobs

- Governor to Destination: probe live load: runtime.queries for Trino, job slots for EMR

- Governor to Redis: compute free slots from caps, off-peak window, budget and cooldown

- Governor to Worker: admit and dispatch the job

- Worker to PostgreSQL: read the latest scan and partitions

- Worker to Planner: build the MaintenancePlan from mode and thresholds

- Worker to Destination: render dialect SQL and execute it, partition-filtered

- Worker to PostgreSQL: write audit and result

- Worker to Redis: update live counters and state

- Governor to PostgreSQL: finalize the run summary as ok, failed or deferred

## Job lifecycle

Nothing runs until the governor admits the job. A failure stays on that table and the run continues. A deferred job goes back to the queue and is retried when its window opens.

Figure 4Every job ends recorded, even when it fails or waits

*A job ends as recorded, whether it succeeded, failed or was skipped.*

- **Read path

- **Write or execute

- **Control or probe
Text description
- Queued to Admitted

- Admitted to Running

- Running to Succeeded

- Queued to Deferred (deadline)

- Deferred to Queued (re-queue)

- Running to Failed (recorded)

The run view groups jobs by how they ended. This one shows 9 jobs: 8 completed, 1 failed and 1 skipped. The failed job stays on its table.

cryo.local/runs/7f3c9a21

*Seeded demo lake, not a production fleet. The failed job is isolated to its table.*

## Assignment

Assigning a preset to many tables sends a filter spec, never a page of table ids. The API resolves the filter, shows a preview, and only then creates the binding.

Figure 5Filter, assign, preview: nothing runs until you confirm

*An assignment carries a filter, so selecting 'all matching tables' works across pages.*

- **Write or execute

- **Read path

- **Control or probe
Text description
- Filter tables to Select all matching

- Select all matching to Assign preset

- Assign preset to Preview plan

- Preview plan to Run

- Assign preset to Resolve filter (preset)

- Preview plan to Build plan (dry run)

## Component jobs

| Component | Job | Boundary |
| --- | --- | --- |

| **Scanner** | Reads sources and stores the shape of every table. Runs from a CronJob and from an ad-hoc scan queue. | Never reads data files. Never starts a rewrite. |

| **Planner** | Turns stored table shape into ordered operations with partition predicates. | Engine-agnostic. Emits no SQL strings. Operation order is fixed in `cryo/planner/guardrails.py`. |

| **Scheduler** | Finds due presets and enqueues jobs. | Leader-elected. Several replicas may start, one is active. |

| **Governor** | Decides whether and when a job may run. Probes live load (`system.runtime.queries` for Trino, slots for Spark on EMR), applies per-replica caps and round-robins across replicas. | Leader-elected. Honors cooldowns and off-peak windows. Bounds real concurrency, so more workers do not mean more parallel rewrites. |

| **Worker** | Executes one table's maintenance on one destination engine. | Holds a per-table Redis lock for the job's lifetime. A failure is recorded and the run continues. |

| **API and web UI** | The API serves tables, runs, presets, assignments and audit. The UI is a client of the API. | The UI talks only to the API. |

Interactive deep dives

Standalone viewers with route tracing and a presentation mode: [system architecture](https://tech-pw.github.io/cryo/diagrams/system.architecture.html?present=1), [maintenance sequence](https://tech-pw.github.io/cryo/diagrams/maintenance.sequence.html?present=1), [job lifecycle](https://tech-pw.github.io/cryo/diagrams/job.lifecycle.html?present=1), [scan data flow](https://tech-pw.github.io/cryo/diagrams/scan.dataflow.html?present=1) and [assignment workflow](https://tech-pw.github.io/cryo/diagrams/assign.workflow.html?present=1).

## Where to go next

How Cryo fitsSource, preset and destination: the three parts that never collapse into one.MaintenanceThe fixed order a run walks and the guardrails that stay on.DeploymentWhat these components look like as Kubernetes workloads.ReferenceFeature presence, public surfaces and what is in the box.

Source: https://tech-pw.github.io/cryo/architecture/
Markdown: https://tech-pw.github.io/cryo/architecture/index.md
