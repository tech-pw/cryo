//! Error types for Cryo Native

use thiserror::Error;

#[derive(Error, Debug)]
pub enum CryoError {
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
    
    #[error("Arrow error: {0}")]
    Arrow(#[from] arrow::error::ArrowError),
    
    #[error("Parquet error: {0}")]
    Parquet(#[from] parquet::errors::ParquetError),
    
    #[error("DataFusion error: {0}")]
    DataFusion(#[from] datafusion::error::DataFusionError),
    
    #[error("Invalid configuration: {0}")]
    InvalidConfig(String),
    
    #[error("Compaction failed: {0}")]
    CompactionFailed(String),
}
