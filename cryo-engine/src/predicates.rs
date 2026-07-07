//! Partition predicate evaluation for native compaction scope enforcement.
//!
//! Cryo planner emits OR-of-AND partition predicates (parity with Trino/Spark renderers).
//! Compaction must only touch data files whose partition values match the planned scope.

use crate::{OperationRequest, PredicateRequest, PredicateTermRequest};
use anyhow::{Context, Result};
use iceberg::spec::{DataFile, Literal, PrimitiveLiteral, Transform};
use iceberg::table::Table;

pub fn operation_has_predicates(op: &OperationRequest) -> bool {
    op.predicates.iter().any(|predicate| !predicate.terms.is_empty())
}

/// Returns a human-readable reason when execute-mode compaction cannot honor predicates.
pub fn unsupported_predicate_reason(op: &OperationRequest) -> Option<String> {
    for predicate in &op.predicates {
        for term in &predicate.terms {
            if let Some(reason) = term_unsupported_reason(term) {
                return Some(reason);
            }
        }
    }
    None
}

fn term_unsupported_reason(term: &PredicateTermRequest) -> Option<String> {
    match term.transform.as_str() {
        "bucket" | "truncate" | "unsupported" => Some(format!(
            "rewrite_data_files does not support partition transform '{}' on column '{}'",
            term.transform, term.base_column
        )),
        "identity" | "day" | "month" | "year" => None,
        other => Some(format!(
            "rewrite_data_files does not support partition transform '{other}'"
        )),
    }
}

/// True when the data file matches any OR-branch predicate (each branch is AND of terms).
pub fn matches_any_predicate(data_file: &DataFile, table: &Table, predicates: &[PredicateRequest]) -> Result<bool> {
    if predicates.is_empty() {
        return Ok(true);
    }
    for predicate in predicates {
        if predicate.terms.is_empty() {
            continue;
        }
        if matches_predicate(data_file, table, predicate)? {
            return Ok(true);
        }
    }
    Ok(false)
}

fn matches_predicate(
    data_file: &DataFile,
    table: &Table,
    predicate: &PredicateRequest,
) -> Result<bool> {
    for term in &predicate.terms {
        if !matches_term(data_file, table, term)? {
            return Ok(false);
        }
    }
    Ok(true)
}

fn matches_term(data_file: &DataFile, table: &Table, term: &PredicateTermRequest) -> Result<bool> {
    let partition = data_file.partition();
    let spec = table.metadata().default_partition_spec();
    let field_index = resolve_partition_field_index(spec.fields(), term)
        .with_context(|| format!("no partition field for predicate term {}", term.base_column))?;
    let field_value = partition.fields().get(field_index);
    if term.value.is_null() {
        return Ok(field_value.is_none() || field_value.and_then(|value| value.as_ref()).is_none());
    }
    let literal = field_value
        .and_then(|value| value.as_ref())
        .context("partition value missing for predicate field")?;
    Ok(literal_matches_term(literal, term))
}

fn resolve_partition_field_index(
    fields: &[iceberg::spec::PartitionField],
    term: &PredicateTermRequest,
) -> Option<usize> {
    let candidates = candidate_field_names(term);
    for (index, field) in fields.iter().enumerate() {
        if candidates.iter().any(|name| name == &field.name) {
            return Some(index);
        }
    }
    // Fall back to transform-aware match when names differ (e.g. spec uses source column).
    for (index, field) in fields.iter().enumerate() {
        if field_matches_term(field, term) {
            return Some(index);
        }
    }
    None
}

fn field_matches_term(field: &iceberg::spec::PartitionField, term: &PredicateTermRequest) -> bool {
    let transform = term.transform.as_str();
    field.name == term.base_column
        || (transform == "identity" && field.transform == Transform::Identity)
        || (transform == "day" && matches!(field.transform, Transform::Day))
        || (transform == "month" && matches!(field.transform, Transform::Month))
        || (transform == "year" && matches!(field.transform, Transform::Year))
}

fn candidate_field_names(term: &PredicateTermRequest) -> Vec<String> {
    let mut names = Vec::new();
    if let Some(partition_field) = &term.partition_field {
        names.push(partition_field.clone());
    }
    let base = term.base_column.as_str();
    match term.transform.as_str() {
        "identity" => names.push(base.to_string()),
        "day" => {
            names.push(format!("{base}_day"));
            names.push(base.to_string());
        }
        "month" => {
            names.push(format!("{base}_month"));
            names.push(base.to_string());
        }
        "year" => {
            names.push(format!("{base}_year"));
            names.push(base.to_string());
        }
        "bucket" => {
            names.push(format!("{base}_bucket"));
            names.push(base.to_string());
        }
        other => {
            names.push(format!("{base}_{other}"));
            names.push(base.to_string());
        }
    }
    names.sort();
    names.dedup();
    names
}

fn literal_matches_term(literal: &Literal, term: &PredicateTermRequest) -> bool {
    match term.transform.as_str() {
        "identity" => identity_literal_matches(literal, &term.value),
        "day" | "month" | "year" => numeric_literal_matches(literal, &term.value),
        _ => false,
    }
}

fn identity_literal_matches(literal: &Literal, expected: &serde_json::Value) -> bool {
    match literal {
        Literal::Primitive(PrimitiveLiteral::String(actual)) => {
            expected.as_str().map(|v| v == actual).unwrap_or(false)
        }
        Literal::Primitive(PrimitiveLiteral::Int(actual)) => json_as_i64(expected) == Some(*actual as i64),
        Literal::Primitive(PrimitiveLiteral::Long(actual)) => json_as_i64(expected) == Some(*actual),
        Literal::Primitive(PrimitiveLiteral::Boolean(actual)) => {
            expected.as_bool().map(|v| v == *actual).unwrap_or(false)
        }
        _ => false,
    }
}

fn numeric_literal_matches(literal: &Literal, expected: &serde_json::Value) -> bool {
    let Some(expected_num) = json_as_i64(expected) else {
        return false;
    };
    match literal {
        Literal::Primitive(PrimitiveLiteral::Int(actual)) => *actual as i64 == expected_num,
        Literal::Primitive(PrimitiveLiteral::Long(actual)) => *actual == expected_num,
        _ => false,
    }
}

fn json_as_i64(value: &serde_json::Value) -> Option<i64> {
    value.as_i64().or_else(|| value.as_u64().map(|v| v as i64))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn candidate_field_names_day_suffix() {
        let term = PredicateTermRequest {
            base_column: "event_date".to_string(),
            transform: "day".to_string(),
            value: serde_json::json!(20337),
            col_type: Some("date".to_string()),
        };
        let names = candidate_field_names(&term);
        assert!(names.contains(&"event_date_day".to_string()));
    }

    #[test]
    fn numeric_literal_matches_epoch_day() {
        let literal = Literal::Primitive(PrimitiveLiteral::Int(20337));
        assert!(numeric_literal_matches(
            &literal,
            &serde_json::json!(20337)
        ));
    }

    #[test]
    fn bucket_transform_is_unsupported() {
        let term = PredicateTermRequest {
            base_column: "user_id".to_string(),
            transform: "bucket".to_string(),
            value: serde_json::json!(3),
            col_type: None,
        };
        assert!(term_unsupported_reason(&term).is_some());
    }
}
