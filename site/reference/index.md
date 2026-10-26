---
title: Reference
description: Feature presence compared with DIY scripts, Amoro, LakeOps, Crunch, Databricks and managed cloud services, plus Cryo's public surfaces.
canonical: https://tech-pw.github.io/cryo/reference/
updated: 2026-10-26
---

# Reference

Cryo is an Apache-2.0, self-hosted Apache Iceberg maintenance platform. This page lists what is in the box, what is not, and how it compares.

## Feature presence, not a ranking

A filled mark is an unqualified yes in the project README. A half mark means partial, limited or capability-gated. An empty mark means no, or that the cell is not documented or not applicable.

Source: README comparison table, Amoro cells checked against apache/amoro 6d3cd42. For Cryo, sorting is Spark only and Hilbert is capability-gated. For DIY, maintenance and admission exist only if you build them. Amoro sorting honours table sort order and has no Z-order. Amoro admission is quota-based, not live engine load. Amoro health is a numeric score, not an A to F grade, and has no cost projection. For managed cloud services, sorting and health grading vary and admission is not applicable. Crunch health reporting is savings-focused, not an A to F grade. Databricks cells are predictive optimization, checked against its documentation updated 11 September 2026: it runs only on Unity Catalog managed tables (Delta Lake and Iceberg), not external tables, and runs on Databricks serverless compute, so admission is not applicable. It sets clustering keys through automatic liquid clustering, reports operations and cost in a system table without a grade, and is enabled by account, catalog, schema or table.

| Capability | Cryo | DIY | Amoro | LakeOps | Crunch | Databricks | Managed cloud |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Maintenance operations | Yes | Partial | Yes | Yes | Yes | Yes | Yes |
| Sorting or clustering | Partial | No | Partial | Yes | Yes | Yes | No |
| Compression below ZSTD | No | No | No | No | Yes | No | No |
| Load-aware admission | Yes | No | No | Yes | No | No | No |
| Health grade and cost | Yes | No | Partial | Partial | No | Partial | No |
| Data leaves your infrastructure | No | No | No | Yes | No | Yes | Yes |
| Fleet assignment UI | Yes | No | Limited | Yes | Yes | Yes | Yes |

## Public surfaces

The web UI talks only to the API. The scanner, scheduler, governor and workers are reached through the API and never directly from a browser.

Figure 1Everything goes through the API

*The API is the only public entry point.*

- **Read path
Text description
- Web UI to API (REST)
- API to Platform services

The API groups its resources as `tables`, `runs`, `presets`, `assignments`, `audit`, `sources` and `destinations`. The OpenAPI document is served at `/openapi.json`, and the web client's types are generated from it.

## Launch tier

Cryo is pre-1.0. The launch-tier surface is the scanner, planner, governor, Trino and Spark engines, presets, audit, and the run console. Native Go compaction is a configured path, not assumed on.

## Where to go next

ArchitectureCatalogs on the read side, engines on the write side, Cryo in the middle.MaintenanceThe planner emits a plan with no SQL strings. The order is fixed in code.DeploymentSelf-hosted on Kubernetes with your engines where they are.GitHubThe Apache-2.0 source is public. Issues and pull requests are open.

Source: https://tech-pw.github.io/cryo/reference/
Markdown: https://tech-pw.github.io/cryo/reference/index.md
