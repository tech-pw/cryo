//! Cryo Native Engine
//!
//! High-performance Iceberg table compaction and data layout optimization
//! using Apache DataFusion and Arrow.

pub mod compaction;
pub mod error;
pub mod storage;

use pyo3::prelude::*;

/// Python module for cryo-native
#[pymodule]
fn cryo_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyCompactionEngine>()?;
    m.add_class::<PyCompactionResult>()?;
    m.add_class::<PyCompactionConfig>()?;
    Ok(())
}

/// Compaction configuration exposed to Python
#[pyclass]
#[derive(Clone)]
pub struct PyCompactionConfig {
    /// Target file size in bytes
    #[pyo3(get, set)]
    pub target_file_size_bytes: u64,
    
    /// Minimum file size in bytes (files smaller than this are candidates for compaction)
    #[pyo3(get, set)]
    pub min_file_size_bytes: u64,
    
    /// Maximum file size in bytes
    #[pyo3(get, set)]
    pub max_file_size_bytes: u64,
    
    /// Maximum number of files to process in one compaction run
    #[pyo3(get, set)]
    pub max_files_per_run: usize,
    
    /// Columns to sort by (empty for binpack only)
    #[pyo3(get, set)]
    pub sort_columns: Vec<String>,
    
    /// Whether to use z-order instead of linear sort
    #[pyo3(get, set)]
    pub use_zorder: bool,
    
    /// Partition filter (SQL WHERE clause)
    #[pyo3(get, set)]
    pub partition_filter: Option<String>,
}

#[pymethods]
impl PyCompactionConfig {
    #[new]
    #[pyo3(signature = (
        target_file_size_bytes = 268435456,
        min_file_size_bytes = 67108864,
        max_file_size_bytes = 536870912,
        max_files_per_run = 1000,
        sort_columns = Vec::new(),
        use_zorder = false,
        partition_filter = None
    ))]
    fn new(
        target_file_size_bytes: u64,
        min_file_size_bytes: u64,
        max_file_size_bytes: u64,
        max_files_per_run: usize,
        sort_columns: Vec<String>,
        use_zorder: bool,
        partition_filter: Option<String>,
    ) -> Self {
        Self {
            target_file_size_bytes,
            min_file_size_bytes,
            max_file_size_bytes,
            max_files_per_run,
            sort_columns,
            use_zorder,
            partition_filter,
        }
    }
}

impl Default for PyCompactionConfig {
    fn default() -> Self {
        Self {
            target_file_size_bytes: 256 * 1024 * 1024,  // 256 MB
            min_file_size_bytes: 64 * 1024 * 1024,      // 64 MB
            max_file_size_bytes: 512 * 1024 * 1024,     // 512 MB
            max_files_per_run: 1000,
            sort_columns: Vec::new(),
            use_zorder: false,
            partition_filter: None,
        }
    }
}

/// Result of a compaction operation
#[pyclass]
#[derive(Clone)]
pub struct PyCompactionResult {
    /// Whether the operation succeeded
    #[pyo3(get)]
    pub success: bool,
    
    /// Number of input files processed
    #[pyo3(get)]
    pub input_files: usize,
    
    /// Number of output files created
    #[pyo3(get)]
    pub output_files: usize,
    
    /// Total bytes read
    #[pyo3(get)]
    pub bytes_read: u64,
    
    /// Total bytes written
    #[pyo3(get)]
    pub bytes_written: u64,
    
    /// Duration in milliseconds
    #[pyo3(get)]
    pub duration_ms: u64,
    
    /// Error message if failed
    #[pyo3(get)]
    pub error: Option<String>,
    
    /// Output file paths
    #[pyo3(get)]
    pub output_paths: Vec<String>,
}

#[pymethods]
impl PyCompactionResult {
    fn __repr__(&self) -> String {
        if self.success {
            format!(
                "CompactionResult(success=True, input_files={}, output_files={}, \
                 bytes_read={}, bytes_written={}, duration_ms={})",
                self.input_files, self.output_files,
                self.bytes_read, self.bytes_written, self.duration_ms
            )
        } else {
            format!(
                "CompactionResult(success=False, error='{}')",
                self.error.as_deref().unwrap_or("unknown")
            )
        }
    }
}

/// Native compaction engine
#[pyclass]
pub struct PyCompactionEngine {
    runtime: tokio::runtime::Runtime,
}

#[pymethods]
impl PyCompactionEngine {
    #[new]
    fn new() -> PyResult<Self> {
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
        
        Ok(Self { runtime })
    }
    
    /// Compact Parquet files using binpack strategy
    fn binpack_files(
        &self,
        input_paths: Vec<String>,
        output_dir: String,
        config: PyCompactionConfig,
    ) -> PyResult<PyCompactionResult> {
        self.runtime.block_on(async {
            compaction::binpack_files(&input_paths, &output_dir, &config.into())
                .await
                .map(Into::into)
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
        })
    }
    
    /// Compact and sort Parquet files
    fn sort_files(
        &self,
        input_paths: Vec<String>,
        output_dir: String,
        sort_columns: Vec<String>,
        config: PyCompactionConfig,
    ) -> PyResult<PyCompactionResult> {
        let mut cfg: compaction::CompactionConfig = config.into();
        cfg.sort_columns = sort_columns;
        cfg.use_zorder = false;
        
        self.runtime.block_on(async {
            compaction::sort_files(&input_paths, &output_dir, &cfg)
                .await
                .map(Into::into)
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
        })
    }
    
    /// Compact files with z-order clustering
    fn zorder_files(
        &self,
        input_paths: Vec<String>,
        output_dir: String,
        zorder_columns: Vec<String>,
        config: PyCompactionConfig,
    ) -> PyResult<PyCompactionResult> {
        let mut cfg: compaction::CompactionConfig = config.into();
        cfg.sort_columns = zorder_columns;
        cfg.use_zorder = true;
        
        self.runtime.block_on(async {
            compaction::zorder_files(&input_paths, &output_dir, &cfg)
                .await
                .map(Into::into)
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
        })
    }
    
    /// Analyze files to determine optimal compaction strategy
    fn analyze_files(
        &self,
        input_paths: Vec<String>,
    ) -> PyResult<pyo3::Py<pyo3::types::PyDict>> {
        use pyo3::types::PyDict;
        
        Python::with_gil(|py| {
            let result = self.runtime.block_on(async {
                compaction::analyze_files(&input_paths).await
            }).map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
            
            let dict = PyDict::new_bound(py);
            dict.set_item("total_files", result.total_files)?;
            dict.set_item("total_bytes", result.total_bytes)?;
            dict.set_item("avg_file_size", result.avg_file_size)?;
            dict.set_item("small_files", result.small_files)?;
            dict.set_item("recommended_strategy", result.recommended_strategy)?;
            dict.set_item("estimated_output_files", result.estimated_output_files)?;
            
            Ok(dict.into())
        })
    }
}

impl From<PyCompactionConfig> for compaction::CompactionConfig {
    fn from(py_config: PyCompactionConfig) -> Self {
        Self {
            target_file_size_bytes: py_config.target_file_size_bytes,
            min_file_size_bytes: py_config.min_file_size_bytes,
            max_file_size_bytes: py_config.max_file_size_bytes,
            max_files_per_run: py_config.max_files_per_run,
            sort_columns: py_config.sort_columns,
            use_zorder: py_config.use_zorder,
            partition_filter: py_config.partition_filter,
        }
    }
}

impl From<compaction::CompactionResult> for PyCompactionResult {
    fn from(result: compaction::CompactionResult) -> Self {
        Self {
            success: result.success,
            input_files: result.input_files,
            output_files: result.output_files,
            bytes_read: result.bytes_read,
            bytes_written: result.bytes_written,
            duration_ms: result.duration_ms,
            error: result.error,
            output_paths: result.output_paths,
        }
    }
}
