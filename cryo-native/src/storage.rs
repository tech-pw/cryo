//! Storage abstraction for local and cloud storage

use std::sync::Arc;

use object_store::aws::AmazonS3Builder;
use object_store::local::LocalFileSystem;
use object_store::ObjectStore;

use crate::error::CryoError;

/// Storage configuration
#[derive(Clone, Debug)]
pub struct StorageConfig {
    /// Storage type: "local", "s3", "gcs", "azure"
    pub storage_type: String,
    
    /// Base path or bucket
    pub base_path: String,
    
    /// AWS region (for S3)
    pub region: Option<String>,
    
    /// AWS access key (for S3)
    pub access_key: Option<String>,
    
    /// AWS secret key (for S3)
    pub secret_key: Option<String>,
    
    /// Endpoint URL (for S3-compatible)
    pub endpoint: Option<String>,
}

impl Default for StorageConfig {
    fn default() -> Self {
        Self {
            storage_type: "local".to_string(),
            base_path: "/tmp".to_string(),
            region: None,
            access_key: None,
            secret_key: None,
            endpoint: None,
        }
    }
}

/// Create an object store from configuration
pub fn create_object_store(config: &StorageConfig) -> Result<Arc<dyn ObjectStore>, CryoError> {
    match config.storage_type.as_str() {
        "local" => {
            Ok(Arc::new(LocalFileSystem::new()))
        }
        "s3" => {
            let mut builder = AmazonS3Builder::new()
                .with_bucket_name(&config.base_path);
            
            if let Some(region) = &config.region {
                builder = builder.with_region(region);
            }
            
            if let (Some(key), Some(secret)) = (&config.access_key, &config.secret_key) {
                builder = builder
                    .with_access_key_id(key)
                    .with_secret_access_key(secret);
            }
            
            if let Some(endpoint) = &config.endpoint {
                builder = builder.with_endpoint(endpoint);
            }
            
            let store = builder.build()
                .map_err(|e| CryoError::InvalidConfig(e.to_string()))?;
            
            Ok(Arc::new(store))
        }
        _ => Err(CryoError::InvalidConfig(format!(
            "Unsupported storage type: {}",
            config.storage_type
        ))),
    }
}

/// Parse a path to determine storage type and location
pub fn parse_path(path: &str) -> (String, String) {
    if path.starts_with("s3://") {
        let rest = &path[5..];
        if let Some(idx) = rest.find('/') {
            let bucket = &rest[..idx];
            let key = &rest[idx + 1..];
            return ("s3".to_string(), format!("{}/{}", bucket, key));
        }
        return ("s3".to_string(), rest.to_string());
    }
    
    if path.starts_with("gs://") {
        let rest = &path[5..];
        return ("gcs".to_string(), rest.to_string());
    }
    
    if path.starts_with("az://") || path.starts_with("azure://") {
        let rest = path.split("://").nth(1).unwrap_or(path);
        return ("azure".to_string(), rest.to_string());
    }
    
    ("local".to_string(), path.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_parse_s3_path() {
        let (storage_type, path) = parse_path("s3://my-bucket/path/to/file.parquet");
        assert_eq!(storage_type, "s3");
        assert_eq!(path, "my-bucket/path/to/file.parquet");
    }
    
    #[test]
    fn test_parse_local_path() {
        let (storage_type, path) = parse_path("/local/path/to/file.parquet");
        assert_eq!(storage_type, "local");
        assert_eq!(path, "/local/path/to/file.parquet");
    }
}
