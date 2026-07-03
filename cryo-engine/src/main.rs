mod operations;
mod progress;

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::io::{self, Read};
use tracing::{info, Level};
use tracing_subscriber::fmt::format::FmtSpan;

#[derive(Debug, Deserialize)]
pub struct ExecuteRequest {
    pub table_ref: String,
    pub catalog_uri: String,
    pub warehouse: String,
    pub mode: String,
    pub operations: Vec<OperationRequest>,
    #[serde(default)]
    pub params: serde_json::Value,
}

#[derive(Debug, Clone, Deserialize)]
pub struct OperationRequest {
    #[serde(rename = "type")]
    pub operation_type: String,
    #[serde(default)]
    pub params: serde_json::Value,
}

#[derive(Debug, Serialize)]
pub struct ExecuteResponse {
    pub ok: bool,
    pub table_ref: String,
    pub operations: usize,
    pub results: Vec<OperationResult>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct OperationResult {
    pub operation: String,
    pub success: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub files_rewritten: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bytes_removed: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub snapshots_expired: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub manifests_rewritten: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    pub duration_ms: u64,
}

fn main() {
    tracing_subscriber::fmt()
        .with_max_level(Level::INFO)
        .with_span_events(FmtSpan::CLOSE)
        .json()
        .with_writer(io::stderr)
        .init();

    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 || args[1] != "execute" {
        eprintln!("usage: cryo-engine execute < request.json");
        std::process::exit(2);
    }

    let mut input = String::new();
    io::stdin()
        .read_to_string(&mut input)
        .expect("failed to read stdin");

    let request: ExecuteRequest = match serde_json::from_str(&input) {
        Ok(req) => req,
        Err(e) => {
            let response = ExecuteResponse {
                ok: false,
                table_ref: String::new(),
                operations: 0,
                results: vec![],
                error: Some(format!("failed to parse request: {e}")),
            };
            println!("{}", serde_json::to_string(&response).unwrap());
            std::process::exit(1);
        }
    };

    let runtime = tokio::runtime::Runtime::new().expect("failed to create tokio runtime");
    let response = runtime.block_on(execute(request));

    println!("{}", serde_json::to_string(&response).unwrap());
    std::process::exit(if response.ok { 0 } else { 1 });
}

async fn execute(request: ExecuteRequest) -> ExecuteResponse {
    info!(
        table_ref = %request.table_ref,
        operations = request.operations.len(),
        mode = %request.mode,
        "starting maintenance execution"
    );

    let mut results = Vec::new();
    let mut all_ok = true;

    for op in &request.operations {
        let start = std::time::Instant::now();
        let result = execute_operation(&request, op).await;
        let duration_ms = start.elapsed().as_millis() as u64;

        match result {
            Ok(mut op_result) => {
                op_result.duration_ms = duration_ms;
                results.push(op_result);
            }
            Err(e) => {
                all_ok = false;
                results.push(OperationResult {
                    operation: op.operation_type.clone(),
                    success: false,
                    files_rewritten: None,
                    bytes_removed: None,
                    snapshots_expired: None,
                    manifests_rewritten: None,
                    error: Some(e.to_string()),
                    duration_ms,
                });
            }
        }
    }

    ExecuteResponse {
        ok: all_ok,
        table_ref: request.table_ref,
        operations: results.len(),
        results,
        error: None,
    }
}

async fn execute_operation(
    request: &ExecuteRequest,
    op: &OperationRequest,
) -> Result<OperationResult> {
    info!(operation = %op.operation_type, "executing operation");

    match op.operation_type.as_str() {
        "optimize" | "rewrite_data_files" => {
            operations::rewrite_data_files(request, op)
                .await
                .context("rewrite_data_files failed")
        }
        "expire_snapshots" => {
            operations::expire_snapshots(request, op)
                .await
                .context("expire_snapshots failed")
        }
        "remove_orphan_files" => {
            operations::remove_orphan_files(request, op)
                .await
                .context("remove_orphan_files failed")
        }
        "optimize_manifests" | "rewrite_manifests" => {
            operations::rewrite_manifests(request, op)
                .await
                .context("rewrite_manifests failed")
        }
        other => {
            anyhow::bail!("unsupported operation type: {other}")
        }
    }
}
