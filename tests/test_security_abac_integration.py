"""Integration tests for ABAC row filters and column masks."""

from __future__ import annotations

from uuid import uuid4

from cryo.security.abac import ABACContext, ABACPolicy, apply_column_masks, evaluate_row_filter


def test_evaluate_row_filter_substitutes_user_region():
    """Row filter placeholders should be replaced with user attribute values."""
    policy = ABACPolicy(
        table_id=uuid4(),
        role="analyst",
        row_filter="region = current_user_region()",
        column_masks={},
    )
    context = ABACContext(
        user_id="user-1",
        user_role="analyst",
        user_attributes={"region": "US-WEST"},
    )

    filter_sql = evaluate_row_filter(policy, context)

    assert filter_sql == "region = 'US-WEST'"


def test_evaluate_row_filter_substitutes_department():
    """Department placeholder should resolve from user attributes."""
    policy = ABACPolicy(
        table_id=uuid4(),
        role="finance_analyst",
        row_filter="department = current_user_department()",
        column_masks={},
    )
    context = ABACContext(
        user_id="user-2",
        user_role="finance_analyst",
        user_attributes={"department": "finance"},
    )

    filter_sql = evaluate_row_filter(policy, context)

    assert filter_sql == "department = 'finance'"


def test_evaluate_row_filter_returns_none_when_unconfigured():
    """Policies without row_filter should return None."""
    policy = ABACPolicy(
        table_id=uuid4(),
        role="engineer",
        row_filter=None,
        column_masks={},
    )
    context = ABACContext(
        user_id="user-3",
        user_role="engineer",
        user_attributes={},
    )

    assert evaluate_row_filter(policy, context) is None


def test_apply_column_masks_masks_sensitive_columns():
    """Column masks should replace sensitive columns with masking expressions."""
    policy = ABACPolicy(
        table_id=uuid4(),
        role="analyst",
        row_filter=None,
        column_masks={
            "ssn": "REGEXP_REPLACE(ssn, '\\\\d{5}$', 'XXXXX')",
            "email": "CONCAT(SUBSTR(email, 1, 3), '***')",
        },
    )

    masked = apply_column_masks(policy, ["customer_id", "ssn", "email"])

    assert masked["customer_id"] == "customer_id"
    assert "REGEXP_REPLACE" in masked["ssn"]
    assert masked["email"] == "CONCAT(SUBSTR(email, 1, 3), '***')"


def test_apply_column_masks_passes_through_unmasked_columns():
    """Columns without a mask entry should pass through unchanged."""
    policy = ABACPolicy(
        table_id=uuid4(),
        role="viewer",
        row_filter=None,
        column_masks={"ssn": "NULL"},
    )

    masked = apply_column_masks(policy, ["order_id", "amount"])

    assert masked == {"order_id": "order_id", "amount": "amount"}
