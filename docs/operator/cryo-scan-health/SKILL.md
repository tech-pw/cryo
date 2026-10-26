---
name: cryo-scan-health
description: Explain a Cryo scan failure, a broken table, or a stale freshness number. Use when a scan is partial or a table will not scan.
---

# Cryo scan health

Separate a broken table from a scanner failure. The classifier is display-only. It does not change scan behavior.

Read [cryo/scanner/failure_classifier.py](../../cryo/scanner/failure_classifier.py). Do not invent a category.

## Categories

`FailureCategory` is `permission_denied`, `metadata_not_found`, `manifest_missing`, `other`. None of them is actionable from inside Cryo.

| Class | Permanent | Meaning |
|---|---|---|
| `manifest_missing` | yes | Metadata was read. The manifest list it names is gone. Trino fails the same way. The owning team repoints the catalog or restores the table. |
| `metadata_not_found` | yes | The catalog entry points at a metadata file that is not in object storage. Do not drop the table from this error alone. |
| `permission_denied` | no | Cryo was denied reading the current metadata file. An IAM change can fix it. For `gs://`, Cryo uses GCP Application Default Credentials only. It does not accept GCS keys, does not price GCS, and does not compact `gs://`. |
| `other` | no | Unclassified. Read the raw error. A wrong confident category is worse than "Other". |

`_PERMANENTLY_UNREADABLE` is exactly `metadata_not_found` and `manifest_missing`. A rescan returns the same error. Permission denied is absent from that set on purpose: burying it in "unreadable" hides a misconfiguration that can be fixed.

A bare 403 is classified before the 404 markers. A 403 that also names a missing manifest is permission denied, not unreadable.

A 404 is not Glacier. The module docstring records that zero of thousands of broken tables contained `INVALID_OBJECT_STATE`, which is the code Glacier and Deep Archive actually return (HeadObject succeeds, GetObject fails). The incident doc's 404 was a genuine `NoSuchKey`, not `AccessDenied` wearing a 404. Those are different sentences in different files. Do not swap them.

Add a category only after the AWS error code it keys on has been seen in real `last_scan_error` text.

## A partial scan that is not a broken table

- Assignment is `index % num_chunks == chunk_id`. The CronJob sets `--chunk-id` from `JOB_COMPLETION_INDEX`, and sets `completions`, `parallelism`, and `--num-chunks` from `scanner.chunks`. A `--num-chunks` larger than `completions` drops the unclaimed chunks with no error. Read [deploy/helm/cryo/templates/scanner-cronjob.yaml](../../deploy/helm/cryo/templates/scanner-cronjob.yaml) before calling the scanner flaky.
- The 6-hourly sweep is the CronJob (`python -m cryo.scanner.run`). It does not use the ad-hoc scanner Deployment. That Deployment can sit at 0 replicas only on the KEDA path (`keda.minReplicas: 0`). The HPA floor is 3, and the Deployment replica count still applies when KEDA is off. Zero is not what an HPA install should show.
- `deferred` is incremented only from `scan_retry_deferred`. It is retry backoff, not a failed table.

## Refuse

- Do not tell the user to rescan `manifest_missing` or `metadata_not_found`.
- Do not query live `$files` or list a production metastore to confirm a stored error.
- Do not add a failure category the classifier does not have.
- Do not run maintenance from a scan error.
