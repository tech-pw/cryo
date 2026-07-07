from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cryo.domain.enums import OperationType, PartitionTransform
from cryo.domain.models import (
    MaintenancePlan,
    Operation,
    PartitionPredicate,
    PartitionPredicateTerm,
)

_NATIVE_OPERATION_TYPES = {
    OperationType.EXPIRE_SNAPSHOTS: "expire_snapshots",
    OperationType.REMOVE_ORPHAN_FILES: "remove_orphan_files",
    OperationType.OPTIMIZE: "rewrite_data_files",
    OperationType.OPTIMIZE_MANIFESTS: "rewrite_manifests",
}

# Source config keys forwarded to cryo-engine catalog/storage builders.
_CATALOG_PROPERTY_KEYS = (
    "s3.endpoint",
    "s3.access-key-id",
    "s3.secret-access-key",
    "s3.path-style-access",
    "s3.region",
    "token",
    "credential",
    "oauth2-server-uri",
    "scope",
    "rest.sigv4-enabled",
    "rest.signing-name",
    "rest.signing-region",
)


def plan_to_payload(plan: MaintenancePlan) -> dict[str, Any]:
    return {
        "table_ref": plan.table_ref,
        "mode": plan.mode.value,
        "operations": [_operation_payload(operation) for operation in plan.operations],
    }


def native_table_ref(table_ref: str) -> str:
    """Strip display catalog prefix for REST catalog namespace.table lookups."""
    parts = table_ref.split(".")
    if len(parts) >= 3:
        return ".".join(parts[1:])
    return table_ref


def source_catalog_config(source_config: Mapping[str, Any]) -> dict[str, Any]:
    uri = source_config.get("uri")
    warehouse = source_config.get("warehouse")
    if not isinstance(uri, str) or not uri:
        raise ValueError("source config missing uri")
    if not isinstance(warehouse, str) or not warehouse:
        raise ValueError("source config missing warehouse")
    payload: dict[str, Any] = {
        "catalog_uri": uri,
        "warehouse": warehouse,
    }
    endpoint = source_config.get("s3.endpoint")
    access_key = source_config.get("s3.access-key-id")
    secret_key = source_config.get("s3.secret-access-key")
    if isinstance(endpoint, str) and endpoint:
        payload["s3_endpoint"] = endpoint
    if isinstance(access_key, str) and access_key:
        payload["s3_access_key"] = access_key
    if isinstance(secret_key, str) and secret_key:
        payload["s3_secret_key"] = secret_key

    catalog_properties: dict[str, str] = {}
    for key in _CATALOG_PROPERTY_KEYS:
        value = source_config.get(key)
        if isinstance(value, str) and value:
            catalog_properties[key] = value
    for key, value in source_config.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value:
            continue
        if key.startswith("s3.") and key not in catalog_properties:
            catalog_properties[key] = value
    if catalog_properties:
        payload["catalog_properties"] = catalog_properties
    return payload


def normalize_native_operation_params(operation: Operation) -> dict[str, Any]:
    """Map planner params to cryo-engine operation contracts."""
    params = dict(operation.params)
    if operation.type is OperationType.OPTIMIZE:
        if "target_file_size_bytes" not in params:
            file_size_mb = params.get("file_size_mb")
            if file_size_mb is not None:
                params["target_file_size_bytes"] = int(file_size_mb) * 1024 * 1024
    return params


def build_execute_request(
    *,
    plan: MaintenancePlan,
    operation: Operation,
    source_config: Mapping[str, Any],
    destination_config: Mapping[str, Any],
) -> dict[str, Any]:
    return build_execute_request_for_operations(
        plan=plan,
        operations=(operation,),
        source_config=source_config,
        destination_config=destination_config,
    )


def build_execute_request_for_operations(
    *,
    plan: MaintenancePlan,
    operations: tuple[Operation, ...],
    source_config: Mapping[str, Any],
    destination_config: Mapping[str, Any],
) -> dict[str, Any]:
    if not operations:
        raise ValueError("at least one operation is required")

    mode = str(destination_config.get("operation_mode", "execute"))
    request = source_catalog_config(source_config)
    operation_payloads: list[dict[str, Any]] = []
    for operation in operations:
        native_type = _NATIVE_OPERATION_TYPES.get(operation.type)
        if native_type is None:
            raise ValueError(f"unsupported native operation: {operation.type.value}")
        operation_payloads.append(
            {
                "type": native_type,
                "params": normalize_native_operation_params(operation),
                **_predicate_payload(operation),
            }
        )

    request.update(
        {
            "table_ref": native_table_ref(plan.table_ref),
            "mode": mode,
            "operations": operation_payloads,
        }
    )
    return request


def operation_to_dict(operation: Operation) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": operation.type.value,
        "params": normalize_native_operation_params(operation),
    }
    if operation.predicates is not None:
        payload["predicates"] = [predicate_to_dict(predicate) for predicate in operation.predicates]
    return payload


def operation_from_dict(raw: Mapping[str, object]) -> Operation | None:
    op_type = raw.get("type")
    if not isinstance(op_type, str):
        return None
    try:
        operation_type = OperationType(op_type)
    except ValueError:
        return None
    params = raw.get("params")
    predicates_raw = raw.get("predicates")
    predicates: tuple[PartitionPredicate, ...] | None = None
    if isinstance(predicates_raw, list):
        parsed = [predicate_from_dict(item) for item in predicates_raw]
        predicates = tuple(item for item in parsed if item is not None)
    return Operation(
        type=operation_type,
        params=dict(params) if isinstance(params, dict) else {},
        predicates=predicates,
    )


def predicate_to_dict(predicate: PartitionPredicate) -> dict[str, Any]:
    return {
        "unsupported_reason": predicate.unsupported_reason,
        "terms": [
            {
                "base_column": term.base_column,
                "transform": term.transform.value,
                "value": term.value,
                "col_type": term.col_type,
                "transform_param": term.transform_param,
                "partition_field": term.partition_field,
                "unsupported_reason": term.unsupported_reason,
            }
            for term in predicate.terms
        ],
    }


def predicate_from_dict(raw: object) -> PartitionPredicate | None:
    if not isinstance(raw, dict):
        return None
    terms_raw = raw.get("terms")
    if not isinstance(terms_raw, list):
        return None
    terms: list[PartitionPredicateTerm] = []
    for item in terms_raw:
        if not isinstance(item, dict):
            continue
        transform_raw = item.get("transform")
        if not isinstance(transform_raw, str):
            continue
        try:
            transform = PartitionTransform(transform_raw)
        except ValueError:
            continue
        base_column = item.get("base_column")
        if not isinstance(base_column, str):
            continue
        terms.append(
            PartitionPredicateTerm(
                base_column=base_column,
                transform=transform,
                value=item.get("value"),
                col_type=str(item.get("col_type") or ""),
                transform_param=item.get("transform_param")
                if isinstance(item.get("transform_param"), int)
                else None,
                partition_field=item.get("partition_field")
                if isinstance(item.get("partition_field"), str)
                else None,
                unsupported_reason=item.get("unsupported_reason")
                if isinstance(item.get("unsupported_reason"), str)
                else None,
            )
        )
    unsupported_reason = raw.get("unsupported_reason")
    return PartitionPredicate(
        terms=tuple(terms),
        unsupported_reason=unsupported_reason if isinstance(unsupported_reason, str) else None,
    )


def _operation_payload(operation: Operation) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": operation.type.value,
        "params": dict(operation.params),
    }
    predicate_payload = _predicate_payload(operation)
    if predicate_payload:
        payload.update(predicate_payload)
    return payload


def _predicate_payload(operation: Operation) -> dict[str, Any]:
    if not operation.predicates:
        return {}
    return {
        "predicates": [
            {
                "terms": [
                    {
                        "base_column": term.base_column,
                        "transform": term.transform.value,
                        "value": term.value,
                        "col_type": term.col_type,
                        **(
                            {"partition_field": term.partition_field}
                            if term.partition_field
                            else {}
                        ),
                    }
                    for term in predicate.terms
                ]
            }
            for predicate in operation.predicates
        ]
    }
