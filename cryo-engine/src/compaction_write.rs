//! Binpack compaction: read candidate Parquet via table FileIO, write staged outputs (no commit).

use crate::compaction::CompactionCandidate;
use anyhow::{Context, Result};
use arrow::compute::concat_batches;
use arrow::datatypes::SchemaRef;
use arrow::record_batch::RecordBatch;
use bytes::Bytes;
use iceberg::io::FileIO;
use iceberg::table::Table;
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use parquet::arrow::ArrowWriter;
use parquet::basic::Compression;
use parquet::file::properties::WriterProperties;
use std::sync::Arc;
use tracing::info;
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CompactionWriteResult {
    pub input_files: u64,
    pub output_files: u64,
    pub bytes_read: u64,
    pub bytes_written: u64,
    pub staging_prefix: String,
}

pub async fn binpack_candidates_to_staging(
    table: &Table,
    candidates: &[CompactionCandidate],
    target_file_size_bytes: u64,
) -> Result<CompactionWriteResult> {
    if candidates.is_empty() {
        return Ok(CompactionWriteResult {
            input_files: 0,
            output_files: 0,
            bytes_read: 0,
            bytes_written: 0,
            staging_prefix: String::new(),
        });
    }

    let file_io = table.file_io().clone();
    let mut all_batches: Vec<RecordBatch> = Vec::new();
    let mut schema: Option<SchemaRef> = None;
    let mut bytes_read = 0u64;

    for candidate in candidates {
        let (batches, file_schema, read_bytes) =
            read_parquet_file(&file_io, &candidate.path).await?;
        bytes_read += read_bytes;
        if schema.is_none() {
            schema = Some(file_schema);
        }
        all_batches.extend(batches);
    }

    let schema = schema.unwrap_or_else(|| Arc::new(arrow::datatypes::Schema::empty()));
    if all_batches.is_empty() {
        return Ok(CompactionWriteResult {
            input_files: candidates.len() as u64,
            output_files: 0,
            bytes_read,
            bytes_written: 0,
            staging_prefix: String::new(),
        });
    }

    let combined = concat_batches(&schema, &all_batches)?;
    let staging_prefix = staging_location(table);
    let (output_files, bytes_written) =
        write_binpack_output(&file_io, &staging_prefix, &schema, &combined, target_file_size_bytes)
            .await?;

    info!(
        input_files = candidates.len(),
        output_files,
        bytes_read,
        bytes_written,
        staging_prefix = %staging_prefix,
        "compaction binpack staged (no Iceberg commit)"
    );

    Ok(CompactionWriteResult {
        input_files: candidates.len() as u64,
        output_files,
        bytes_read,
        bytes_written,
        staging_prefix,
    })
}

fn staging_location(table: &Table) -> String {
    let location = table.metadata().location().trim_end_matches('/');
    let run_id = Uuid::new_v4().simple();
    format!("{location}/data/.cryo-compaction-staging/{run_id}")
}

async fn read_parquet_file(
    file_io: &FileIO,
    path: &str,
) -> Result<(Vec<RecordBatch>, SchemaRef, u64)> {
    let input = file_io
        .new_input(path)
        .with_context(|| format!("failed to open input file {path}"))?;
    let data = input
        .read()
        .await
        .with_context(|| format!("failed to read input file {path}"))?;
    let read_bytes = data.len() as u64;
    let builder = ParquetRecordBatchReaderBuilder::try_new(data)
        .with_context(|| format!("failed to parse parquet {path}"))?;
    let schema = builder.schema().clone();
    let reader = builder
        .build()
        .with_context(|| format!("failed to build parquet reader for {path}"))?;
    let batches: Vec<RecordBatch> = reader
        .collect::<std::result::Result<Vec<_>, _>>()
        .with_context(|| format!("failed to read parquet batches from {path}"))?;
    Ok((batches, schema, read_bytes))
}

async fn write_binpack_output(
    file_io: &FileIO,
    staging_prefix: &str,
    schema: &SchemaRef,
    combined: &RecordBatch,
    target_file_size_bytes: u64,
) -> Result<(u64, u64)> {
    let total_rows = combined.num_rows();
    if total_rows == 0 {
        return Ok((0, 0));
    }

    let rows_per_file = estimate_rows_per_file(combined, target_file_size_bytes);
    let props = WriterProperties::builder()
        .set_compression(Compression::SNAPPY)
        .build();

    let mut output_files = 0u64;
    let mut bytes_written = 0u64;
    let mut offset = 0usize;
    let mut part_idx = 0u32;

    while offset < total_rows {
        let end = (offset + rows_per_file).min(total_rows);
        let batch = combined.slice(offset, end - offset);
        let file_path = format!(
            "{staging_prefix}/part-{part_idx:05}-{}.parquet",
            Uuid::new_v4().simple()
        );
        let written = write_parquet_batch(file_io, &file_path, schema, &batch, &props).await?;
        bytes_written += written;
        output_files += 1;
        offset = end;
        part_idx += 1;
    }

    Ok((output_files, bytes_written))
}

fn estimate_rows_per_file(batch: &RecordBatch, target_file_size_bytes: u64) -> usize {
    let total_rows = batch.num_rows();
    if total_rows == 0 {
        return 1;
    }
    let memory_size = batch.get_array_memory_size().max(1);
    let rows = ((target_file_size_bytes as usize).saturating_mul(total_rows) / memory_size).max(1);
    rows.min(total_rows).max(1)
}

async fn write_parquet_batch(
    file_io: &FileIO,
    path: &str,
    schema: &SchemaRef,
    batch: &RecordBatch,
    props: &WriterProperties,
) -> Result<u64> {
    let mut buffer = Vec::new();
    {
        let mut writer = ArrowWriter::try_new(&mut buffer, schema.clone(), Some(props.clone()))
            .with_context(|| format!("failed to create parquet writer for {path}"))?;
        writer
            .write(batch)
            .with_context(|| format!("failed to write batch to {path}"))?;
        writer
            .close()
            .with_context(|| format!("failed to close parquet writer for {path}"))?;
    }
    let output = file_io
        .new_output(path)
        .with_context(|| format!("failed to open output file {path}"))?;
    let size = buffer.len() as u64;
    output
        .write(Bytes::from(buffer))
        .await
        .with_context(|| format!("failed to upload staged parquet {path}"))?;
    Ok(size)
}
