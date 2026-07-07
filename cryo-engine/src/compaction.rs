//! Compaction candidate discovery from live table metadata (no live `$files` scans).

use crate::predicates::{matches_any_predicate, operation_has_predicates};
use crate::OperationRequest;
use anyhow::{Context, Result};
use iceberg::spec::DataContentType;
use iceberg::table::Table;
use tracing::info;

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CompactionScanSummary {
    /// Small data files in scope that are candidates for rewrite.
    pub candidate_files: u64,
    pub candidate_bytes: u64,
    /// All live data files in scope (including files already at target size).
    pub scoped_files: u64,
    pub scoped_bytes: u64,
}

pub fn estimate_output_files(candidate_bytes: u64, target_file_size_bytes: u64, candidate_files: u64) -> u64 {
    if candidate_files == 0 || candidate_bytes == 0 {
        return 0;
    }
    let target = target_file_size_bytes.max(1);
    ((candidate_bytes + target - 1) / target).max(1)
}

pub async fn scan_candidates(
    table: &Table,
    op: &OperationRequest,
    min_file_size_bytes: u64,
) -> Result<CompactionScanSummary> {
    let metadata = table.metadata();
    let current_snapshot = match metadata.current_snapshot() {
        Some(snapshot) => snapshot,
        None => return Ok(CompactionScanSummary::default()),
    };

    let file_io = table.file_io().clone();
    let manifest_list = table
        .manifest_list_reader(current_snapshot)
        .load()
        .await
        .context("failed to load manifest list for compaction scan")?;

    let filter_predicates = operation_has_predicates(op);
    let mut summary = CompactionScanSummary::default();

    for manifest_file in manifest_list.entries() {
        let manifest = manifest_file
            .load_manifest(&file_io)
            .await
            .with_context(|| format!("failed to load manifest {}", manifest_file.manifest_path))?;

        for entry in manifest.entries() {
            if !entry.is_alive() {
                continue;
            }
            let data_file = entry.data_file();
            if data_file.content_type() != DataContentType::Data {
                continue;
            }
            if filter_predicates && !matches_any_predicate(data_file, table, &op.predicates)? {
                continue;
            }

            let file_size = data_file.file_size_in_bytes().max(0) as u64;
            summary.scoped_files += 1;
            summary.scoped_bytes += file_size;

            if file_size < min_file_size_bytes {
                summary.candidate_files += 1;
                summary.candidate_bytes += file_size;
            }
        }
    }

    info!(
        scoped_files = summary.scoped_files,
        scoped_bytes = summary.scoped_bytes,
        candidate_files = summary.candidate_files,
        candidate_bytes = summary.candidate_bytes,
        min_file_size_bytes,
        predicate_branches = op.predicates.len(),
        "compaction candidate scan complete"
    );

    Ok(summary)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn estimate_output_files_rounds_up() {
        assert_eq!(estimate_output_files(300, 128, 3), 3);
        assert_eq!(estimate_output_files(128, 128, 1), 1);
        assert_eq!(estimate_output_files(0, 128, 0), 0);
    }
}
