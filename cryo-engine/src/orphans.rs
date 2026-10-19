//! Orphan file detection and removal for native maintenance.

use crate::ExecuteRequest;
use anyhow::{Context, Result};
use chrono::{Duration, Utc};
use futures::stream::TryStreamExt;
use iceberg::table::Table;
use object_store::aws::AmazonS3Builder;
use object_store::path::Path as ObjectPath;
use object_store::{ObjectMeta, ObjectStore};
use std::collections::HashSet;
use std::sync::Arc;
use tracing::info;

#[derive(Debug, Clone)]
pub struct ListedObject {
    pub path: String,
    pub size: u64,
    pub last_modified_ms: Option<i64>,
}

#[derive(Debug, Clone, Default)]
pub struct OrphanScanSummary {
    pub orphan_count: u64,
    pub orphan_bytes: u64,
    pub listed_count: u64,
}

pub fn normalize_storage_path(path: &str) -> String {
    path.replace("s3a://", "s3://").replace("s3n://", "s3://")
}

/// Collect all file paths referenced by the table's live metadata and snapshots.
pub async fn collect_referenced_paths(table: &Table) -> Result<HashSet<String>> {
    let mut referenced = HashSet::new();
    let metadata = table.metadata();

    referenced.insert(normalize_storage_path(metadata.location()));

    if let Some(metadata_location) = table.metadata_location() {
        referenced.insert(normalize_storage_path(metadata_location));
    }

    for entry in metadata.metadata_log() {
        referenced.insert(normalize_storage_path(&entry.metadata_file));
    }

    for snapshot in metadata.snapshots() {
        referenced.insert(normalize_storage_path(snapshot.manifest_list()));

        let manifest_list = table
            .manifest_list_reader(snapshot)
            .load()
            .await
            .with_context(|| {
                format!(
                    "failed to load manifest list for snapshot {}",
                    snapshot.snapshot_id()
                )
            })?;

        for manifest_file in manifest_list.entries() {
            referenced.insert(normalize_storage_path(&manifest_file.manifest_path));
            let manifest = table
                .manifest_reader()
                .read(manifest_file)
                .await
                .with_context(|| {
                    format!("failed to load manifest {}", manifest_file.manifest_path)
                })?;
            for entry in manifest.entries() {
                if entry.is_alive() {
                    referenced.insert(normalize_storage_path(entry.file_path()));
                }
            }
        }
    }

    Ok(referenced)
}

pub async fn scan_orphans(
    table: &Table,
    request: &ExecuteRequest,
    min_age_hours: i64,
) -> Result<(OrphanScanSummary, Vec<ListedObject>)> {
    let referenced = collect_referenced_paths(table).await?;
    let location = normalize_storage_path(table.metadata().location());
    let listed = list_objects_under_location(request, &location).await?;

    let cutoff_ms =
        Utc::now().timestamp_millis() - Duration::hours(min_age_hours).num_milliseconds();
    let mut orphans = Vec::new();
    let mut summary = OrphanScanSummary {
        listed_count: listed.len() as u64,
        ..Default::default()
    };

    for object in listed {
        let normalized = normalize_storage_path(&object.path);
        if referenced.contains(&normalized) {
            continue;
        }
        if let Some(last_modified_ms) = object.last_modified_ms {
            if last_modified_ms > cutoff_ms {
                continue;
            }
        }
        summary.orphan_count += 1;
        summary.orphan_bytes += object.size;
        orphans.push(object);
    }

    info!(
        location,
        listed = summary.listed_count,
        orphans = summary.orphan_count,
        orphan_bytes = summary.orphan_bytes,
        "orphan scan complete"
    );

    Ok((summary, orphans))
}

pub async fn delete_orphans(table: &Table, orphans: &[ListedObject]) -> Result<(u64, u64)> {
    let file_io = table.file_io();
    let mut deleted_count = 0u64;
    let mut deleted_bytes = 0u64;

    for orphan in orphans {
        file_io
            .delete(&orphan.path)
            .await
            .with_context(|| format!("failed to delete orphan file {}", orphan.path))?;
        deleted_count += 1;
        deleted_bytes += orphan.size;
    }

    Ok((deleted_count, deleted_bytes))
}

async fn list_objects_under_location(
    request: &ExecuteRequest,
    location: &str,
) -> Result<Vec<ListedObject>> {
    let (bucket, prefix) = parse_s3_location(location)
        .with_context(|| format!("unsupported table location for orphan listing: {location}"))?;
    let store = build_object_store(request, &bucket)?;
    let list_prefix = ObjectPath::from(prefix.as_str());

    let mut objects = Vec::new();
    let mut stream = store.list(Some(&list_prefix));
    while let Some(meta) = stream.try_next().await? {
        objects.push(object_meta_to_listed(&bucket, meta)?);
    }
    Ok(objects)
}

fn object_meta_to_listed(bucket: &str, meta: ObjectMeta) -> Result<ListedObject> {
    let path = format!("s3://{}/{}", bucket, meta.location.as_ref());
    Ok(ListedObject {
        path,
        size: meta.size as u64,
        last_modified_ms: Some(meta.last_modified.timestamp_millis()),
    })
}

fn parse_s3_location(location: &str) -> Result<(String, String)> {
    let normalized = normalize_storage_path(location);
    let rest = normalized
        .strip_prefix("s3://")
        .context("table location must use an s3:// scheme")?;
    let (bucket, key) = rest
        .split_once('/')
        .context("table location must include a bucket and key prefix")?;
    let mut prefix = key.to_string();
    if !prefix.is_empty() && !prefix.ends_with('/') {
        prefix.push('/');
    }
    Ok((bucket.to_string(), prefix))
}

fn build_object_store(request: &ExecuteRequest, bucket: &str) -> Result<Arc<dyn ObjectStore>> {
    let mut builder = AmazonS3Builder::new().with_bucket_name(bucket);

    if let Some(endpoint) = &request.s3_endpoint {
        builder = builder.with_endpoint(endpoint);
        if endpoint.starts_with("http://") {
            builder = builder.with_allow_http(true);
        }
    }
    if let Some(access_key) = &request.s3_access_key {
        builder = builder.with_access_key_id(access_key);
    }
    if let Some(secret_key) = &request.s3_secret_key {
        builder = builder.with_secret_access_key(secret_key);
    }

    let region = request
        .catalog_properties
        .get("s3.region")
        .cloned()
        .or_else(|| request.catalog_properties.get("region").cloned())
        .unwrap_or_else(|| "us-east-1".to_string());
    builder = builder.with_region(region);

    let path_style = request
        .catalog_properties
        .get("s3.path-style-access")
        .map(|value| value == "true")
        .unwrap_or(false);
    if path_style {
        builder = builder.with_virtual_hosted_style_request(false);
    }

    let store = builder
        .build()
        .context("failed to build object store for orphan listing")?;
    Ok(Arc::new(store))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_s3_scheme_aliases() {
        assert_eq!(
            normalize_storage_path("s3a://bucket/path"),
            "s3://bucket/path"
        );
    }

    #[test]
    fn parse_table_location() {
        let (bucket, prefix) =
            parse_s3_location("s3://warehouse/db/table").expect("parse location");
        assert_eq!(bucket, "warehouse");
        assert_eq!(prefix, "db/table/");
    }
}
