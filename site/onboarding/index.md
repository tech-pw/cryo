---
title: Connect a lakehouse
description: Connect a REST, Hive Metastore, Glue, Polaris or Nessie catalog to Cryo, choose S3 credentials, and run the first scan. Covers every field, what the scan reads, and the failures that look like success.
canonical: https://tech-pw.github.io/cryo/onboarding/
updated: 2026-10-16
---

# Connect a lakehouse

Wire a catalog into Cryo, then scan it. The connection test proves reachability; the scan is what fills the UI.

Two separate steps

Connecting a catalog tells Cryo *what tables exist*. Scanning reads those tables and stores their file counts, sizes and grades. A connected catalog with no scan leaves the UI empty — that is expected, not a failure.

## What you need

Cryo reads catalogs and object storage. It needs two things that are often owned by different people, so collect both before you start:

- **Catalog endpoint** — a REST URI, a Hive Metastore thrift address, or an AWS region for Glue.

- **Read access to the buckets your tables live in** — an IAM role is the production answer. Catalog access alone is not enough: the catalog records *where* tables are, object storage holds the files Cryo measures.

## Connect a catalog

Open **Settings → Connections → Lakehouses** and choose **New lakehouse**. The drawer walks three steps: Connect, Storage, Review. Later steps stay closed until the connection test passes, because nothing after it can be filled in sensibly without a working catalog.

### Identity and catalog

Name the lakehouse anything you will recognise in a run log. **Catalog type** decides which other fields matter, and **Catalog URI** takes the endpoint for that type — the placeholder shows the expected shape.

**Warehouse** is the root location the catalog stores tables under. Leave it blank for Hive: Cryo reads it from the catalog's own database locations when you test the connection, and fills the field in. Set it yourself and Cryo will not overwrite it.

**Catalog name** is optional. It overrides the name PyIceberg registers the catalog under; blank uses the lakehouse name.

### Test the connection

This makes one `list_namespaces()` call. It proves the endpoint is reachable and the credentials are accepted, nothing more — it deliberately does not enumerate tables, which is slow enough on a large metastore to be unusable as a button a human waits on.

Done when the panel reports the schema count. That number is also the first honest signal that you pointed at the catalog you meant to.

### Storage credentials

Once the test passes, Cryo walks the catalog to find which buckets your tables actually live in, and shows them ranked by how many tables each holds. Buckets appear as they are found rather than after the whole walk, so you can pick one without waiting.

Each bucket is read-probed with the credentials on this screen and labelled **Readable**, **No access** or **Not found**. Resolve the ones marked no-access here — see [authentication](#auth) below.

### Review and save

The summary restates what will be stored, including whether the connection test passed. Save is disabled until it has.

Done when the lakehouse appears in the Lakehouses list with an **Enabled** badge, its endpoint and its S3 auth method. No tables are visible yet; that is the scan's job.

## Catalog types

Each type needs a different endpoint. Everything else on the form is shared.

| Type | Catalog URI | Notes |
| --- | --- | --- |

| REST catalog | `http://host:8181` | Any Iceberg REST implementation. Pass OAuth credentials or custom headers through **Advanced properties**. |

| Hive Metastore | `thrift://host:9083` | Cryo reads table locations over Thrift directly, so it still sees tables whose Iceberg metadata will not load. Warehouse is inferred for this type. |

| AWS Glue | Not used | No URI field. Region comes from the storage section; **Warehouse** takes an `s3://` path. |

| Apache Polaris | `http://host:8181/api/catalog` | REST under the hood. Credentials go in Advanced properties. |

| Project Nessie | `http://host:19120/api/v1` | REST under the hood. Branch or ref selection belongs in Advanced properties. |

## S3 authentication

Three methods, in the order you should prefer them.

| Method | Use when | Fields |
| --- | --- | --- |

| **Default credentials** | Production. Cryo runs with an IAM role (IRSA on EKS, instance profile on EC2) and needs no secrets in its database. | Region. Optionally an endpoint. |

| **Access key + secret** | Local development and MinIO. Avoid in production — the secret is stored, and rotating it means editing every lakehouse that uses it. | Access key ID, secret access key. Usually a custom endpoint and path-style access. |

| **Assume IAM role** | The buckets live in another AWS account. | Role ARN. External ID and session name optional. |

A catalog that connects can still fail every scan

Catalog permissions and bucket permissions are granted separately, often by different teams. The connection test cannot detect a storage problem — it never touches S3. That is why the storage step probes each bucket and says plainly when Cryo cannot read it. A bucket marked **No access** will fail every table stored in it, however healthy the catalog looks.

On Kubernetes the usual cause is the pod's ServiceAccount lacking an `eks.amazonaws.com/role-arn` annotation, so AWS calls fall back to the node instance role. Set `serviceAccount.annotations` in the chart, and make sure the role's trust policy names the namespace and ServiceAccount actually in use.

## Limiting what gets scanned

**Schemas for maintenance** restricts scanning and maintenance to the schemas you choose. Leave it empty to include every schema in the catalog.

Worth setting on a large or shared metastore: an unscoped scan walks every namespace, which is slower and pulls in schemas nobody intends Cryo to maintain. The list is populated from the connection test, so it offers real schema names rather than free text.

## The first scan

Connecting stores configuration. Scanning is what produces data — until a scan completes, Tables, Home and Cost are all empty.

### What a scan reads

For every table in scope the scanner records the facts maintenance decisions are made from:

- **File counts and sizes** — total files, how many fall below the small-file threshold, and bytes on disk. This is what drives compaction decisions.

- **Snapshots and manifests** — how much history has accumulated, which is what expiry and manifest rewrites act on.

- **Partition layout** — so maintenance can target individual partitions instead of rewriting whole tables.

- **A health grade** — the letter grade on Home and Tables, derived from the measurements above.

Scanning is read-only. It never rewrites data, and it does not schedule maintenance: that needs a preset assigned to the table.

### Running one

Trigger a scan from the lakehouse row, or let the scheduled scan pick it up — the chart's default is every six hours. Watch progress under **Runs**.

The progress panel distinguishes four outcomes, and the difference matters when something is wrong:

| Counter | Meaning |
| --- | --- |

| **Discovered** | Tables the catalog reported in scope. If this is zero, the problem is the catalog or the schema filter, not storage. |

| **Scanned** | Tables successfully measured. These are the rows that appear in the UI. |

| **Skipped** | Deliberately not measured — most often unchanged since the last scan. |

| **Failed** | Tables Cryo could not read. The live log names the table and the reason. |

Reading a failure count

A high failure count with a healthy **Discovered** number almost always means storage, not catalog: Cryo found the tables and could not read their files. Open the live log and read the first failure — one cause usually explains all of them.

`source config missing warehouse` means the lakehouse has no warehouse root, so table paths cannot be resolved. Edit the lakehouse, clear the field, and re-test the connection to have it filled in.

`AccessDenied` on an S3 path means credentials, not configuration. Re-open the storage step to see which buckets are readable.

## After the first scan

- **Tables** lists every scanned table with its grade and the counts behind it.

- **Home** shows last night's maintenance, what needs you next, and grades across the fleet.

- **Runs** keeps the receipt for each scan, including what failed and why.

Nothing is maintained yet. Assign a preset to decide what Cryo is allowed to do, then see [Maintenance](https://tech-pw.github.io/cryo/maintenance/) for how plans are built and admitted.

Source: https://tech-pw.github.io/cryo/onboarding/
Markdown: https://tech-pw.github.io/cryo/onboarding/index.md
