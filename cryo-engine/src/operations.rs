//! Iceberg maintenance operations implemented with iceberg-rust.

use crate::catalog_preview::PreviewCatalog;
use crate::compaction::{collect_candidates, estimate_output_files, scan_candidates};
use crate::compaction_write::binpack_candidates_to_staging;
use crate::orphans::{delete_orphans, scan_orphans};
use crate::predicates::{operation_has_predicates, unsupported_predicate_reason};
use crate::progress::{emit_completed, emit_progress, emit_started};
use crate::{ExecuteRequest, OperationRequest, OperationResult};
use anyhow::{Context, Result};
use chrono::{Duration, Utc};
use std::collections::HashSet;
use std::sync::Arc;
use tracing::info;

use iceberg::table::Table;
use iceberg::transaction::{ApplyTransactionAction, Transaction};
use iceberg::{Catalog, CatalogBuilder, NamespaceIdent, TableIdent};
use iceberg_catalog_rest::{
    RestCatalogBuilder, REST_CATALOG_PROP_URI, REST_CATALOG_PROP_WAREHOUSE,
};
use iceberg_storage_opendal::OpenDalStorageFactory;

const DEFAULT_TARGET_FILE_SIZE_BYTES: u64 = 268_435_456; // 256 MB
const DEFAULT_MIN_FILE_SIZE_BYTES: u64 = 104_857_600; // 100 MB
const DEFAULT_ORPHAN_MIN_AGE_HOURS: i64 = 72;

fn is_execute_mode(mode: &str) -> bool {
    !matches!(
        mode.to_ascii_lowercase().as_str(),
        "dry_run" | "dry-run" | "analyze"
    )
}

async fn load_catalog(request: &ExecuteRequest) -> Result<Arc<dyn Catalog>> {
    use std::collections::HashMap;

    let mut props = HashMap::new();
    props.insert(
        REST_CATALOG_PROP_URI.to_string(),
        request.catalog_uri.clone(),
    );
    props.insert(
        REST_CATALOG_PROP_WAREHOUSE.to_string(),
        request.warehouse.clone(),
    );

    if let Some(s3_endpoint) = &request.s3_endpoint {
        props.insert("s3.endpoint".to_string(), s3_endpoint.clone());
    }
    if let Some(s3_access_key) = &request.s3_access_key {
        props.insert("s3.access-key-id".to_string(), s3_access_key.clone());
    }
    if let Some(s3_secret_key) = &request.s3_secret_key {
        props.insert("s3.secret-access-key".to_string(), s3_secret_key.clone());
    }
    for (key, value) in &request.catalog_properties {
        props.insert(key.clone(), value.clone());
    }

    let catalog = RestCatalogBuilder::default()
        .with_storage_factory(Arc::new(OpenDalStorageFactory::S3 {
            customized_credential_load: None,
        }))
        .load("rest", props)
        .await
        .context("failed to connect to REST catalog")?;

    Ok(Arc::new(catalog))
}

/// Parse `schema.table`, `catalog.schema.table`, or multi-level `catalog.ns1.ns2.table`.
/// When three or more segments are present, the first segment is treated as a display catalog
/// prefix from Cryo and dropped; the last segment is the table name.
fn parse_table_ident(table_ref: &str) -> Result<TableIdent> {
    let parts: Vec<&str> = table_ref.split('.').collect();
    if parts.len() < 2 {
        anyhow::bail!(
            "invalid table_ref: {table_ref} (expected namespace.table or catalog.namespace.table)"
        );
    }
    let table_name = parts[parts.len() - 1].to_string();
    let namespace_parts: Vec<String> = if parts.len() >= 3 {
        parts[1..parts.len() - 1]
            .iter()
            .map(|part| (*part).to_string())
            .collect()
    } else {
        vec![parts[0].to_string()]
    };
    let namespace = NamespaceIdent::from_strs(namespace_parts)
        .context(format!("invalid namespace in table_ref: {table_ref}"))?;
    Ok(TableIdent::new(namespace, table_name))
}

fn snapshot_ids(table: &Table) -> HashSet<i64> {
    table
        .metadata()
        .snapshots()
        .map(|snapshot| snapshot.snapshot_id())
        .collect()
}

async fn load_table(request: &ExecuteRequest) -> Result<(Arc<dyn Catalog>, Table)> {
    let catalog = load_catalog(request).await?;
    let table_ident = parse_table_ident(&request.table_ref)?;
    let table = catalog
        .load_table(&table_ident)
        .await
        .context("failed to load table")?;
    Ok((catalog, table))
}

fn build_expire_transaction(table: &Table, op: &OperationRequest) -> Result<Transaction> {
    let tx = Transaction::new(table);
    let mut action = tx.expire_snapshots();
    if let Some(retention_days) = op.params.get("retention_days").and_then(|v| v.as_i64()) {
        let cutoff_ms = (Utc::now() - Duration::days(retention_days)).timestamp_millis();
        action = action.expire_older_than_ms(cutoff_ms);
    }
    if let Some(retain_last) = op.params.get("retain_last").and_then(|v| v.as_u64()) {
        action = action.retain_last(retain_last as usize);
    }
    action
        .apply(Transaction::new(table))
        .context("failed to build expire_snapshots transaction")
}

fn resolve_target_file_size_bytes(op: &OperationRequest) -> u64 {
    op.params
        .get("target_file_size_bytes")
        .and_then(|v| v.as_u64())
        .or_else(|| {
            op.params
                .get("file_size_mb")
                .and_then(|v| v.as_u64())
                .map(|mb| mb.saturating_mul(1024 * 1024))
        })
        .unwrap_or(DEFAULT_TARGET_FILE_SIZE_BYTES)
}

fn resolve_min_file_size_bytes(op: &OperationRequest) -> u64 {
    op.params
        .get("min_file_size_bytes")
        .and_then(|v| v.as_u64())
        .or_else(|| {
            op.params
                .get("min_input_files")
                .and_then(|v| v.as_u64())
                .map(|_| DEFAULT_MIN_FILE_SIZE_BYTES)
        })
        .unwrap_or(DEFAULT_MIN_FILE_SIZE_BYTES)
}

fn compaction_execute_enabled(request: &ExecuteRequest, op: &OperationRequest) -> bool {
    let env_enabled = matches!(
        std::env::var("CRYO_NATIVE_COMPACTION_EXECUTE")
            .ok()
            .as_deref()
            .map(str::to_ascii_lowercase)
            .as_deref(),
        Some("1") | Some("true") | Some("yes")
    );
    if env_enabled {
        return true;
    }
    request
        .params
        .get("compaction_execute")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
        || op
            .params
            .get("compaction_execute")
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
}

fn upstream_commit_blocked_message(staging_detail: Option<&str>) -> String {
    let base = "rewrite_data_files commit blocked upstream on pinned iceberg-rust \
                  (RewriteFilesAction / EPIC #2269)";
    match staging_detail {
        Some(detail) => format!("{base}; {detail}"),
        None => format!("{base}; analyze mode reports planned scope only"),
    }
}

fn rewrite_result(
    success: bool,
    input_files: u64,
    output_files: u64,
    candidate_files: u64,
    candidate_bytes: u64,
    bytes_written: u64,
    error: Option<String>,
) -> OperationResult {
    OperationResult {
        operation: "rewrite_data_files".to_string(),
        success,
        files_rewritten: Some(candidate_files),
        input_files: Some(input_files),
        output_files: Some(output_files),
        bytes_removed: Some(candidate_bytes),
        bytes_written: Some(bytes_written),
        snapshots_expired: None,
        manifests_rewritten: None,
        error,
        duration_ms: 0,
    }
}

pub async fn rewrite_data_files(
    request: &ExecuteRequest,
    op: &OperationRequest,
) -> Result<OperationResult> {
    emit_started("rewrite_data_files");

    let target_file_size = resolve_target_file_size_bytes(op);
    let min_file_size = resolve_min_file_size_bytes(op);

    info!(
        table = %request.table_ref,
        target_file_size,
        min_file_size,
        mode = %request.mode,
        predicate_count = op.predicates.len(),
        "rewrite_data_files"
    );

    if is_execute_mode(&request.mode) {
        if let Some(reason) = unsupported_predicate_reason(op) {
            emit_completed("rewrite_data_files", 0, 0);
            return Ok(rewrite_result(false, 0, 0, 0, 0, 0, Some(reason)));
        }
    }

    let (_catalog, table) = load_table(request).await?;
    let scan = scan_candidates(&table, op, min_file_size).await?;
    let estimated_output =
        estimate_output_files(scan.candidate_bytes, target_file_size, scan.candidate_files);

    if scan.scoped_files == 0 && table.metadata().current_snapshot().is_none() {
        emit_completed("rewrite_data_files", 0, 0);
        return Ok(rewrite_result(true, 0, 0, 0, 0, 0, None));
    }

    emit_progress(
        "rewrite_data_files",
        50,
        scan.candidate_files,
        scan.candidate_bytes,
    );

    if !is_execute_mode(&request.mode) {
        emit_completed(
            "rewrite_data_files",
            scan.candidate_files,
            scan.candidate_bytes,
        );
        return Ok(rewrite_result(
            true,
            scan.candidate_files,
            estimated_output,
            scan.candidate_files,
            scan.candidate_bytes,
            0,
            None,
        ));
    }

    if scan.candidate_files == 0 {
        emit_completed("rewrite_data_files", 0, 0);
        return Ok(rewrite_result(true, 0, 0, 0, 0, 0, None));
    }

    if compaction_execute_enabled(request, op) {
        let candidates = collect_candidates(&table, op, min_file_size).await?;
        let write_result =
            binpack_candidates_to_staging(&table, &candidates, target_file_size).await?;
        emit_completed(
            "rewrite_data_files",
            write_result.input_files,
            write_result.bytes_written,
        );
        let staging_detail = format!(
            "staged {} output file(s) ({} bytes) under {}",
            write_result.output_files, write_result.bytes_written, write_result.staging_prefix
        );
        return Ok(rewrite_result(
            false,
            write_result.input_files,
            write_result.output_files,
            write_result.input_files,
            write_result.bytes_read,
            write_result.bytes_written,
            Some(upstream_commit_blocked_message(Some(&staging_detail))),
        ));
    }

    emit_completed(
        "rewrite_data_files",
        scan.candidate_files,
        scan.candidate_bytes,
    );
    Ok(rewrite_result(
        false,
        scan.candidate_files,
        estimated_output,
        scan.candidate_files,
        scan.candidate_bytes,
        0,
        Some(upstream_commit_blocked_message(None)),
    ))
}

pub async fn expire_snapshots(
    request: &ExecuteRequest,
    op: &OperationRequest,
) -> Result<OperationResult> {
    emit_started("expire_snapshots");

    let (catalog, table) = load_table(request).await?;
    let before = snapshot_ids(&table);
    let tx = build_expire_transaction(&table, op)?;

    let to_expire = if is_execute_mode(&request.mode) {
        let updated = tx
            .commit(catalog.as_ref())
            .await
            .context("failed to commit expire_snapshots transaction")?;
        before.difference(&snapshot_ids(&updated)).count() as u64
    } else {
        let preview = PreviewCatalog::new(catalog);
        tx.commit(&preview)
            .await
            .context("failed to preview expire_snapshots transaction")?;
        preview.expired_count()
    };

    emit_progress("expire_snapshots", 100, to_expire, 0);
    emit_completed("expire_snapshots", to_expire, 0);
    Ok(OperationResult {
        operation: "expire_snapshots".to_string(),
        success: true,
        files_rewritten: None,
        input_files: None,
        output_files: None,
        bytes_removed: None,
        bytes_written: None,
        snapshots_expired: Some(to_expire),
        manifests_rewritten: None,
        error: None,
        duration_ms: 0,
    })
}

pub async fn remove_orphan_files(
    request: &ExecuteRequest,
    op: &OperationRequest,
) -> Result<OperationResult> {
    emit_started("remove_orphan_files");

    let min_age_hours = op
        .params
        .get("min_age_hours")
        .and_then(|v| v.as_i64())
        .or_else(|| {
            op.params
                .get("orphan_min_age_hours")
                .and_then(|v| v.as_i64())
        })
        .or_else(|| {
            op.params
                .get("retention_days")
                .and_then(|v| v.as_i64())
                .map(|days| days * 24)
        })
        .unwrap_or(DEFAULT_ORPHAN_MIN_AGE_HOURS);

    let (_catalog, table) = load_table(request).await?;
    let location = table.metadata().location();
    info!(
        table = %request.table_ref,
        min_age_hours,
        location,
        mode = %request.mode,
        "remove_orphan_files"
    );

    let (summary, orphans) = scan_orphans(&table, request, min_age_hours).await?;
    emit_progress(
        "remove_orphan_files",
        50,
        summary.orphan_count,
        summary.orphan_bytes,
    );

    if !is_execute_mode(&request.mode) {
        emit_completed(
            "remove_orphan_files",
            summary.orphan_count,
            summary.orphan_bytes,
        );
        return Ok(OperationResult {
            operation: "remove_orphan_files".to_string(),
            success: true,
            files_rewritten: None,
            input_files: Some(summary.listed_count),
            output_files: Some(summary.orphan_count),
            bytes_removed: Some(summary.orphan_bytes),
            bytes_written: None,
            snapshots_expired: None,
            manifests_rewritten: None,
            error: None,
            duration_ms: 0,
        });
    }

    if summary.orphan_count == 0 {
        emit_completed("remove_orphan_files", 0, 0);
        return Ok(OperationResult {
            operation: "remove_orphan_files".to_string(),
            success: true,
            files_rewritten: None,
            input_files: Some(summary.listed_count),
            output_files: Some(0),
            bytes_removed: Some(0),
            bytes_written: None,
            snapshots_expired: None,
            manifests_rewritten: None,
            error: None,
            duration_ms: 0,
        });
    }

    let (deleted_count, deleted_bytes) = delete_orphans(&table, &orphans).await?;
    emit_completed("remove_orphan_files", deleted_count, deleted_bytes);
    Ok(OperationResult {
        operation: "remove_orphan_files".to_string(),
        success: true,
        files_rewritten: None,
        input_files: Some(summary.listed_count),
        output_files: Some(deleted_count),
        bytes_removed: Some(deleted_bytes),
        bytes_written: None,
        snapshots_expired: None,
        manifests_rewritten: None,
        error: None,
        duration_ms: 0,
    })
}

pub async fn rewrite_manifests(
    request: &ExecuteRequest,
    _op: &OperationRequest,
) -> Result<OperationResult> {
    emit_started("rewrite_manifests");

    let (_catalog, table) = load_table(request).await?;
    let metadata = table.metadata();
    let current_snapshot = match metadata.current_snapshot() {
        Some(snapshot) => snapshot,
        None => {
            emit_completed("rewrite_manifests", 0, 0);
            return Ok(OperationResult {
                operation: "rewrite_manifests".to_string(),
                success: true,
                files_rewritten: None,
                input_files: None,
                output_files: None,
                bytes_removed: None,
                bytes_written: None,
                snapshots_expired: None,
                manifests_rewritten: Some(0),
                error: None,
                duration_ms: 0,
            });
        }
    };

    let manifest_list = current_snapshot.manifest_list();
    let manifest_count = table
        .manifest_list_reader(&current_snapshot)
        .load()
        .await
        .map(|list| list.entries().len() as u64)
        .unwrap_or(0);
    info!(
        manifest_list = %manifest_list,
        manifest_count,
        mode = %request.mode,
        "rewrite_manifests analyze-only on pinned iceberg-rust"
    );

    if is_execute_mode(&request.mode) {
        emit_completed("rewrite_manifests", 0, 0);
        return Ok(OperationResult {
            operation: "rewrite_manifests".to_string(),
            success: false,
            files_rewritten: None,
            input_files: None,
            output_files: None,
            bytes_removed: None,
            bytes_written: None,
            snapshots_expired: None,
            manifests_rewritten: Some(0),
            error: Some(
                "rewrite_manifests execute is not available on pinned iceberg-rust \
                 (RewriteManifestsAction commit path blocked upstream; issue #2556)"
                    .to_string(),
            ),
            duration_ms: 0,
        });
    }

    emit_progress("rewrite_manifests", 100, manifest_count, 0);
    emit_completed("rewrite_manifests", manifest_count, 0);
    Ok(OperationResult {
        operation: "rewrite_manifests".to_string(),
        success: true,
        files_rewritten: None,
        input_files: Some(manifest_count),
        output_files: Some(manifest_count),
        bytes_removed: None,
        bytes_written: None,
        snapshots_expired: None,
        manifests_rewritten: Some(manifest_count),
        error: None,
        duration_ms: 0,
    })
}

#[cfg(test)]
mod tests {
    use super::parse_table_ident;

    #[test]
    fn parse_schema_table() {
        let ident = parse_table_ident("demo.orders").unwrap();
        assert_eq!(ident.name(), "orders");
    }

    #[test]
    fn parse_catalog_schema_table() {
        let ident = parse_table_ident("rest.demo.orders").unwrap();
        assert_eq!(ident.name(), "orders");
        assert_eq!(ident.namespace().to_string(), "demo");
    }

    #[test]
    fn parse_multi_level_namespace() {
        let ident = parse_table_ident("rest.org.schema.orders").unwrap();
        assert_eq!(ident.name(), "orders");
        assert_eq!(ident.namespace().to_string(), "org.schema");
    }
}
