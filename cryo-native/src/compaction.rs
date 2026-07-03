//! Compaction operations using DataFusion

use std::path::Path;
use std::sync::Arc;
use std::time::Instant;

use arrow::array::{ArrayRef, UInt64Array};
use arrow::compute::concat_batches;
use arrow::datatypes::SchemaRef;
use arrow::record_batch::RecordBatch;
use datafusion::prelude::*;
use datafusion::datasource::file_format::parquet::ParquetFormat;
use datafusion::datasource::listing::{ListingOptions, ListingTable, ListingTableConfig, ListingTableUrl};
use datafusion::execution::context::SessionContext;
use futures::StreamExt;
use parquet::arrow::ArrowWriter;
use parquet::file::properties::WriterProperties;
use parquet::basic::Compression;

use crate::error::CryoError;

/// Configuration for compaction operations
#[derive(Clone, Debug)]
pub struct CompactionConfig {
    pub target_file_size_bytes: u64,
    pub min_file_size_bytes: u64,
    pub max_file_size_bytes: u64,
    pub max_files_per_run: usize,
    pub sort_columns: Vec<String>,
    pub use_zorder: bool,
    pub partition_filter: Option<String>,
}

impl Default for CompactionConfig {
    fn default() -> Self {
        Self {
            target_file_size_bytes: 256 * 1024 * 1024,
            min_file_size_bytes: 64 * 1024 * 1024,
            max_file_size_bytes: 512 * 1024 * 1024,
            max_files_per_run: 1000,
            sort_columns: Vec::new(),
            use_zorder: false,
            partition_filter: None,
        }
    }
}

/// Result of a compaction operation
#[derive(Clone, Debug)]
pub struct CompactionResult {
    pub success: bool,
    pub input_files: usize,
    pub output_files: usize,
    pub bytes_read: u64,
    pub bytes_written: u64,
    pub duration_ms: u64,
    pub error: Option<String>,
    pub output_paths: Vec<String>,
}

/// Analysis result for file statistics
#[derive(Clone, Debug)]
pub struct FileAnalysis {
    pub total_files: usize,
    pub total_bytes: u64,
    pub avg_file_size: u64,
    pub small_files: usize,
    pub recommended_strategy: String,
    pub estimated_output_files: usize,
}

/// Binpack compaction - merge small files without sorting
pub async fn binpack_files(
    input_paths: &[String],
    output_dir: &str,
    config: &CompactionConfig,
) -> Result<CompactionResult, CryoError> {
    let start = Instant::now();
    
    if input_paths.is_empty() {
        return Ok(CompactionResult {
            success: true,
            input_files: 0,
            output_files: 0,
            bytes_read: 0,
            bytes_written: 0,
            duration_ms: 0,
            error: None,
            output_paths: Vec::new(),
        });
    }

    // Create DataFusion context
    let ctx = SessionContext::new();
    
    // Read all input files and get schema
    let (batches, schema, bytes_read) = read_parquet_files(&ctx, input_paths).await?;
    
    if batches.is_empty() {
        return Ok(CompactionResult {
            success: true,
            input_files: input_paths.len(),
            output_files: 0,
            bytes_read,
            bytes_written: 0,
            duration_ms: start.elapsed().as_millis() as u64,
            error: None,
            output_paths: Vec::new(),
        });
    }
    
    // Write output files
    let (output_paths, bytes_written) = write_output_files(
        &batches,
        schema,
        output_dir,
        config.target_file_size_bytes,
    ).await?;
    
    Ok(CompactionResult {
        success: true,
        input_files: input_paths.len(),
        output_files: output_paths.len(),
        bytes_read,
        bytes_written,
        duration_ms: start.elapsed().as_millis() as u64,
        error: None,
        output_paths,
    })
}

/// Sort compaction - merge and sort files
pub async fn sort_files(
    input_paths: &[String],
    output_dir: &str,
    config: &CompactionConfig,
) -> Result<CompactionResult, CryoError> {
    let start = Instant::now();
    
    if input_paths.is_empty() || config.sort_columns.is_empty() {
        return binpack_files(input_paths, output_dir, config).await;
    }

    let ctx = SessionContext::new();
    
    // Read all input files
    let (batches, schema, bytes_read) = read_parquet_files(&ctx, input_paths).await?;
    
    if batches.is_empty() {
        return Ok(CompactionResult {
            success: true,
            input_files: input_paths.len(),
            output_files: 0,
            bytes_read,
            bytes_written: 0,
            duration_ms: start.elapsed().as_millis() as u64,
            error: None,
            output_paths: Vec::new(),
        });
    }
    
    // Concatenate all batches
    let combined = concat_batches(&schema, &batches)?;
    
    // Create a dataframe and sort
    let df = ctx.read_batch(combined)?;
    
    // Build sort expressions
    let sort_exprs: Vec<Expr> = config.sort_columns
        .iter()
        .map(|col| col_name(col).sort(true, false))  // ASC, NULLS LAST
        .collect();
    
    let sorted_df = df.sort(sort_exprs)?;
    
    // Collect sorted results
    let sorted_batches = sorted_df.collect().await?;
    
    // Write output files
    let (output_paths, bytes_written) = write_output_files(
        &sorted_batches,
        schema,
        output_dir,
        config.target_file_size_bytes,
    ).await?;
    
    Ok(CompactionResult {
        success: true,
        input_files: input_paths.len(),
        output_files: output_paths.len(),
        bytes_read,
        bytes_written,
        duration_ms: start.elapsed().as_millis() as u64,
        error: None,
        output_paths,
    })
}

/// Z-order compaction - merge files with z-order clustering
pub async fn zorder_files(
    input_paths: &[String],
    output_dir: &str,
    config: &CompactionConfig,
) -> Result<CompactionResult, CryoError> {
    let start = Instant::now();
    
    if input_paths.is_empty() || config.sort_columns.is_empty() {
        return binpack_files(input_paths, output_dir, config).await;
    }

    let ctx = SessionContext::new();
    
    // Read all input files
    let (batches, schema, bytes_read) = read_parquet_files(&ctx, input_paths).await?;
    
    if batches.is_empty() {
        return Ok(CompactionResult {
            success: true,
            input_files: input_paths.len(),
            output_files: 0,
            bytes_read,
            bytes_written: 0,
            duration_ms: start.elapsed().as_millis() as u64,
            error: None,
            output_paths: Vec::new(),
        });
    }
    
    // Concatenate all batches
    let combined = concat_batches(&schema, &batches)?;
    
    // Compute z-order values for each row
    let zorder_batch = compute_zorder_key(&combined, &config.sort_columns)?;
    
    // Create dataframe with z-order key and sort
    let df = ctx.read_batch(zorder_batch)?;
    let sorted_df = df.sort(vec![col("_zorder_key").sort(true, false)])?;
    
    // Remove the z-order key column and collect
    let columns: Vec<Expr> = schema.fields()
        .iter()
        .map(|f| col_name(f.name()))
        .collect();
    
    let final_df = sorted_df.select(columns)?;
    let sorted_batches = final_df.collect().await?;
    
    // Write output files
    let (output_paths, bytes_written) = write_output_files(
        &sorted_batches,
        schema,
        output_dir,
        config.target_file_size_bytes,
    ).await?;
    
    Ok(CompactionResult {
        success: true,
        input_files: input_paths.len(),
        output_files: output_paths.len(),
        bytes_read,
        bytes_written,
        duration_ms: start.elapsed().as_millis() as u64,
        error: None,
        output_paths,
    })
}

/// Analyze files to determine optimal compaction strategy
pub async fn analyze_files(input_paths: &[String]) -> Result<FileAnalysis, CryoError> {
    let min_file_size = 64 * 1024 * 1024u64; // 64 MB threshold for "small"
    
    let mut total_bytes = 0u64;
    let mut small_files = 0usize;
    
    for path in input_paths {
        let metadata = tokio::fs::metadata(path).await?;
        let size = metadata.len();
        total_bytes += size;
        if size < min_file_size {
            small_files += 1;
        }
    }
    
    let total_files = input_paths.len();
    let avg_file_size = if total_files > 0 {
        total_bytes / total_files as u64
    } else {
        0
    };
    
    // Recommend strategy based on analysis
    let recommended_strategy = if small_files as f64 / total_files as f64 > 0.5 {
        "binpack"
    } else {
        "none"
    };
    
    // Estimate output files (target 256 MB each)
    let target_size = 256 * 1024 * 1024u64;
    let estimated_output_files = ((total_bytes as f64 / target_size as f64).ceil() as usize).max(1);
    
    Ok(FileAnalysis {
        total_files,
        total_bytes,
        avg_file_size,
        small_files,
        recommended_strategy: recommended_strategy.to_string(),
        estimated_output_files,
    })
}

/// Read parquet files and return batches with schema
async fn read_parquet_files(
    ctx: &SessionContext,
    paths: &[String],
) -> Result<(Vec<RecordBatch>, SchemaRef, u64), CryoError> {
    let mut all_batches = Vec::new();
    let mut schema: Option<SchemaRef> = None;
    let mut bytes_read = 0u64;
    
    for path in paths {
        // Get file size
        let metadata = tokio::fs::metadata(path).await?;
        bytes_read += metadata.len();
        
        // Register and query
        let table_name = format!("temp_{}", uuid::Uuid::new_v4().simple());
        ctx.register_parquet(&table_name, path, ParquetReadOptions::default()).await?;
        
        let df = ctx.table(&table_name).await?;
        
        if schema.is_none() {
            schema = Some(df.schema().inner().clone());
        }
        
        let batches = df.collect().await?;
        all_batches.extend(batches);
        
        ctx.deregister_table(&table_name)?;
    }
    
    let schema = schema.unwrap_or_else(|| Arc::new(arrow::datatypes::Schema::empty()));
    Ok((all_batches, schema, bytes_read))
}

/// Write record batches to output files
async fn write_output_files(
    batches: &[RecordBatch],
    schema: SchemaRef,
    output_dir: &str,
    target_file_size: u64,
) -> Result<(Vec<String>, u64), CryoError> {
    use std::fs::File;
    
    std::fs::create_dir_all(output_dir)?;
    
    let mut output_paths = Vec::new();
    let mut bytes_written = 0u64;
    
    // Estimate rows per file based on target size
    let total_rows: usize = batches.iter().map(|b| b.num_rows()).sum();
    let total_bytes: usize = batches.iter().map(|b| b.get_array_memory_size()).sum();
    
    let rows_per_file = if total_bytes > 0 {
        ((target_file_size as usize * total_rows) / total_bytes).max(1000)
    } else {
        100_000
    };
    
    // Combine all batches
    let combined = if !batches.is_empty() {
        concat_batches(&schema, batches)?
    } else {
        return Ok((output_paths, bytes_written));
    };
    
    // Write files
    let mut offset = 0;
    let mut file_idx = 0;
    
    while offset < combined.num_rows() {
        let end = (offset + rows_per_file).min(combined.num_rows());
        let batch = combined.slice(offset, end - offset);
        
        let file_path = format!(
            "{}/part-{:05}-{}.parquet",
            output_dir,
            file_idx,
            uuid::Uuid::new_v4().simple()
        );
        
        let file = File::create(&file_path)?;
        
        let props = WriterProperties::builder()
            .set_compression(Compression::SNAPPY)
            .build();
        
        let mut writer = ArrowWriter::try_new(file, schema.clone(), Some(props))?;
        writer.write(&batch)?;
        writer.close()?;
        
        // Get file size
        let metadata = std::fs::metadata(&file_path)?;
        bytes_written += metadata.len();
        
        output_paths.push(file_path);
        offset = end;
        file_idx += 1;
    }
    
    Ok((output_paths, bytes_written))
}

/// Compute z-order key for clustering
fn compute_zorder_key(batch: &RecordBatch, columns: &[String]) -> Result<RecordBatch, CryoError> {
    use arrow::array::{Array, Int64Array, Float64Array, StringArray};
    
    let num_rows = batch.num_rows();
    
    // For simplicity, we compute a composite sort key by interleaving bits
    // This is a simplified z-order implementation
    let mut zorder_values: Vec<u64> = vec![0; num_rows];
    
    for (col_idx, col_name) in columns.iter().enumerate() {
        if let Ok(idx) = batch.schema().index_of(col_name) {
            let array = batch.column(idx);
            
            // Normalize column values to u64 range
            let normalized = normalize_column(array)?;
            
            // Interleave bits for z-order
            for (row_idx, &val) in normalized.iter().enumerate() {
                zorder_values[row_idx] |= interleave_bits(val, col_idx, columns.len());
            }
        }
    }
    
    // Add z-order key column to batch
    let zorder_array: ArrayRef = Arc::new(UInt64Array::from(zorder_values));
    
    let mut fields = batch.schema().fields().to_vec();
    fields.push(Arc::new(arrow::datatypes::Field::new(
        "_zorder_key",
        arrow::datatypes::DataType::UInt64,
        false,
    )));
    
    let new_schema = Arc::new(arrow::datatypes::Schema::new(fields));
    
    let mut columns: Vec<ArrayRef> = batch.columns().to_vec();
    columns.push(zorder_array);
    
    Ok(RecordBatch::try_new(new_schema, columns)?)
}

/// Normalize a column's values to u64 range for z-ordering
fn normalize_column(array: &ArrayRef) -> Result<Vec<u64>, CryoError> {
    use arrow::array::{AsArray, Int32Array, Int64Array, Float32Array, Float64Array, StringArray};
    use arrow::datatypes::DataType;
    
    let len = array.len();
    let mut result = vec![0u64; len];
    
    match array.data_type() {
        DataType::Int32 => {
            let arr = array.as_any().downcast_ref::<Int32Array>().unwrap();
            for i in 0..len {
                if !arr.is_null(i) {
                    result[i] = (arr.value(i) as i64 + i64::MAX / 2) as u64;
                }
            }
        }
        DataType::Int64 => {
            let arr = array.as_any().downcast_ref::<Int64Array>().unwrap();
            for i in 0..len {
                if !arr.is_null(i) {
                    result[i] = (arr.value(i) as u64).wrapping_add(u64::MAX / 2);
                }
            }
        }
        DataType::Float64 => {
            let arr = array.as_any().downcast_ref::<Float64Array>().unwrap();
            for i in 0..len {
                if !arr.is_null(i) {
                    result[i] = arr.value(i).to_bits();
                }
            }
        }
        DataType::Utf8 => {
            let arr = array.as_any().downcast_ref::<StringArray>().unwrap();
            for i in 0..len {
                if !arr.is_null(i) {
                    // Hash string to u64
                    use std::hash::{Hash, Hasher};
                    let mut hasher = std::collections::hash_map::DefaultHasher::new();
                    arr.value(i).hash(&mut hasher);
                    result[i] = hasher.finish();
                }
            }
        }
        _ => {
            // For other types, use row index as fallback
            for i in 0..len {
                result[i] = i as u64;
            }
        }
    }
    
    Ok(result)
}

/// Interleave bits for z-order computation
fn interleave_bits(value: u64, col_idx: usize, num_cols: usize) -> u64 {
    let bits_per_col = 64 / num_cols.max(1);
    let shift = col_idx * bits_per_col;
    (value >> (64 - bits_per_col)) << shift
}

/// Create column reference
fn col_name(name: &str) -> Expr {
    col(name)
}
