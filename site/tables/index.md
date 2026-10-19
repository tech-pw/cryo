---
title: Tables
description: A guided tour of Cryo's Tables page, covering the triage cards, the nightly maintenance funnel, the catalog list, quick views, filters, columns, the savings view and bulk actions.
canonical: https://tech-pw.github.io/cryo/tables/
updated: 2026-10-18
---

# Tables

Every Iceberg table Cryo has scanned, ordered by what needs you. The top of the page says what last night's maintenance did; the list below says what state each table is in now.

Tables is where you start most days. It answers two questions in order: what did the nightly run do, and which tables need a person? Every screenshot below comes from the seeded demo lake: one REST catalog with 14 active tables, after a nightly run that finished **Partial**.

cryo.local/tables

*The whole page. Triage on top, every table below it.*

## The header

The pill on the right shows the last nightly run's status and how long ago it started. It links to that run, where every job and its audit rows live. If the scanner has fallen behind, so that at least 10% of active tables were last scanned more than 48 hours ago, a banner appears under the header. Health and maintenance decisions for those tables are working from old stats.

## Triage cards and the nightly funnel

cryo.local/tables

*Every number on a card is a link to the tables behind it.*

### Triage cards

Each card counts tables in one state that usually needs a person. Click a card to list those tables below. Click it again to clear the filter.

| Card | Counts tables where |
| --- | --- |

| Failed last run | The latest maintenance job failed or partly failed. |

| Queued or running | A job is waiting for engine capacity or running now. |

| Skipped with work | The table needed maintenance, but the nightly run held it back (cooldown, budget, eligibility). |

| Health dropped | The health score fell 10 or more points in 7 days. |

| Scan issues | The scanner is failing on the table, has never scanned it, or last scanned it more than 48 hours ago. |

### Last nightly run

The Sankey follows every active table through the latest nightly batch: scanned or not, needed work or not, skipped (with the reason) or selected, then how the job ended. It counts **tables, not operations**, so a table with five operations counts once and every column adds up to the active total. In the demo, 13 of 14 tables were scanned; 7 needed work; 3 of those were held back by cooldown, budget and eligibility; and the 4 selected tables split into 2 succeeded, 1 failed and 1 still running.

Click a stage to list its tables. The list shows those tables as they are **now**, so a table rescanned since last night can have moved on.

Why only the last run

An all-time funnel only grows. Scoping it to one batch keeps it a report you can act on: what happened last night, and what is left.

### Catalogs

One row per connected catalog: its whole-number health score, table count, size, modelled storage cost a month and modelled monthly savings. Click a row to filter the list to that catalog. **Cost** opens the cost page. Every dollar figure is a model, as [Health and cost](https://tech-pw.github.io/cryo/health/) explains.

## The table list

cryo.local/tables

*Each row says how healthy the table is, what maintenance is doing about it, and what it would save.*

### Quick views

The chips above the filters are the triage cards as views, plus **Needs work** and **Savings**. Each shows its count. The address bar carries the view as `?view=`, along with the catalog, sort and page, so a link opens the same list.

### Search and filters

The search box matches schema or table names. **Workflow**, **Grade** and **Priority** are multi-select filters; **Catalog** appears too once more than one catalog is connected. **Active only** hides tables the catalog no longer lists. **Ask in plain English** turns a sentence such as "grade F tables over 100 GB" into filters you can review before applying them. **Clear all** resets everything.

cryo.local/tables

*Threshold filters live behind More filters, each with its unit.*

### Columns

| Column | What it shows |
| --- | --- |

| Health | Grade, score, and the change over 7 days (▼ red for a drop, ▲ green for a gain). |

| Maintenance | What Cryo is doing about the table now: Running, Queued, Failed, Skipped, Needs work, Up to date, Held, Excluded, Scan failing or Not scanned, with the reason underneath. |

| Last run | When the last job finished, how it ended, and whether it was nightly or manual. |

| Small files | Small files as a share of all files, with both counts and a bar. |

| Size, Reads 30d | Table size and the number of queries that read it in 30 days. |

| Savings/mo | Modelled monthly saving from maintenance. `-` means none, `<$1` means under a dollar. |

Hover a row for **Scan now** and a shortcut to the table's Maintenance tab.

cryo.local/tables

*Columns are grouped by the question they answer. Choices and widths are saved per browser.*

### Saved views and export

**Saved views** stores the current filters, columns and sort under a name, and can make one your default. Built-in views include **Health triage** (grades D and F), **Biggest tables that need work**, **Most-read tables** and **Top savings opportunities**. **Export** downloads the filtered list as CSV.

## The savings view

cryo.local/tables?view=savings

*Savings first, then the evidence for it.*

The **Savings** quick view lists only tables with a modelled monthly saving, sorted highest first, and puts the money columns first. While it is open, the Columns menu is locked to that layout and says why.

## Acting on many tables

Tick rows to open the selection bar. **Select all N matching** extends the selection to every table the filters match, not just this page. Cryo sends the filter, not a list of IDs, so tables that start matching later are included.

cryo.local/tables

*One row of actions. Anything destructive sits behind the ⋯ menu.*

| Action | What it does |
| --- | --- |

| Assign workflow | Pins the chosen workflow (preset) to the selected tables. Only workflows whose engine can reach those tables' catalog are offered. |

| Preview plan | Shows the plan Cryo would build, without running anything. |

| Scan | Queues a fresh scan of the selected tables. |

| Run maintenance… | Opens the run dialog below. |

| ⋯ → Remove workflow pins | Returns the tables to rule-based assignment, after a confirmation. |

cryo.local/tables

*The run dialog warns about tables that will probably be skipped before you queue anything.*

The run dialog picks the workflow, the operations (the whole workflow in the [standard order](https://tech-pw.github.io/cryo/maintenance/), or manifests only) and the engine. Queued jobs still go through the governor, which paces them against live engine load, so a large selection does not hit the engine all at once. If the button is disabled, the line beside it says why.

## Where to go next

Table detailOpen a row to see the evidence behind its grade and status.Health and costHow the score and the cost model work.MaintenanceOperation order, admission, cooldown and budgets.

Source: https://tech-pw.github.io/cryo/tables/
Markdown: https://tech-pw.github.io/cryo/tables/index.md
