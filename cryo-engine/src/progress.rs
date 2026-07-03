use serde::Serialize;
use std::io::{self, Write};

#[derive(Debug, Serialize)]
pub struct ProgressEvent {
    pub event: &'static str,
    pub operation: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub progress_pct: Option<u8>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub files_processed: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bytes_processed: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
}

impl ProgressEvent {
    pub fn started(operation: &str) -> Self {
        Self {
            event: "started",
            operation: operation.to_string(),
            progress_pct: Some(0),
            files_processed: None,
            bytes_processed: None,
            message: None,
        }
    }

    pub fn progress(operation: &str, pct: u8, files: u64, bytes: u64) -> Self {
        Self {
            event: "progress",
            operation: operation.to_string(),
            progress_pct: Some(pct),
            files_processed: Some(files),
            bytes_processed: Some(bytes),
            message: None,
        }
    }

    pub fn completed(operation: &str, files: u64, bytes: u64) -> Self {
        Self {
            event: "completed",
            operation: operation.to_string(),
            progress_pct: Some(100),
            files_processed: Some(files),
            bytes_processed: Some(bytes),
            message: None,
        }
    }

    pub fn failed(operation: &str, message: &str) -> Self {
        Self {
            event: "failed",
            operation: operation.to_string(),
            progress_pct: None,
            files_processed: None,
            bytes_processed: None,
            message: Some(message.to_string()),
        }
    }

    pub fn emit(&self) {
        if let Ok(json) = serde_json::to_string(self) {
            let _ = writeln!(io::stderr(), "{json}");
        }
    }
}

pub fn emit_started(operation: &str) {
    ProgressEvent::started(operation).emit();
}

pub fn emit_progress(operation: &str, pct: u8, files: u64, bytes: u64) {
    ProgressEvent::progress(operation, pct, files, bytes).emit();
}

pub fn emit_completed(operation: &str, files: u64, bytes: u64) {
    ProgressEvent::completed(operation, files, bytes).emit();
}

pub fn emit_failed(operation: &str, message: &str) {
    ProgressEvent::failed(operation, message).emit();
}
