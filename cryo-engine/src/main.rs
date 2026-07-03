use serde::Deserialize;
use std::io::{self, Read};

#[derive(Debug, Deserialize)]
struct ExecuteRequest {
    table_ref: String,
    mode: String,
    operations: Vec<OperationRequest>,
}

#[derive(Debug, Deserialize)]
struct OperationRequest {
    #[serde(rename = "type")]
    operation_type: String,
    params: serde_json::Value,
}

fn main() {
    if std::env::args().nth(1).as_deref() != Some("execute") {
        eprintln!("usage: cryo-engine execute");
        std::process::exit(2);
    }

    let mut input = String::new();
    io::stdin().read_to_string(&mut input).expect("read stdin");
    let request: ExecuteRequest = serde_json::from_str(&input).expect("parse request");

    // Scaffold: acknowledge supported operations until full iceberg-rust integration lands.
    for operation in &request.operations {
        eprintln!(
            "cryo-engine scaffold: {} on {} ({})",
            operation.operation_type, request.table_ref, request.mode
        );
    }

    println!(
        "{{\"ok\":true,\"table_ref\":\"{}\",\"operations\":{}}}",
        request.table_ref,
        request.operations.len()
    );
}
