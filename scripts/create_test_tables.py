#!/usr/bin/env python3
"""Create five small Iceberg demo tables in the local REST catalog."""

from __future__ import annotations

import sys
from pathlib import Path

from pyspark.sql import Row

# Allow running as `python scripts/create_test_tables.py` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from spark_session import CATALOG_NAME, create_spark  # noqa: E402

NAMESPACE = "demo"
TABLES = ("customers", "products", "orders", "order_items", "shipments")


def main() -> None:
    spark = create_spark("create-test-tables")
    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {CATALOG_NAME}.{NAMESPACE}")

    customers = spark.createDataFrame(
        [
            Row(customer_id=1, name="Alice Nguyen", email="alice@example.com", city="Austin"),
            Row(customer_id=2, name="Bob Patel", email="bob@example.com", city="Denver"),
            Row(customer_id=3, name="Cara Lopez", email="cara@example.com", city="Seattle"),
            Row(customer_id=4, name="Dan Kim", email="dan@example.com", city="Chicago"),
            Row(customer_id=5, name="Eva Rossi", email="eva@example.com", city="Boston"),
        ]
    )
    products = spark.createDataFrame(
        [
            Row(product_id=101, name="Notebook", category="Stationery", price=4.99),
            Row(product_id=102, name="Pen Set", category="Stationery", price=12.50),
            Row(product_id=103, name="Desk Lamp", category="Home", price=29.99),
            Row(product_id=104, name="Coffee Mug", category="Home", price=8.75),
            Row(product_id=105, name="Backpack", category="Travel", price=49.00),
        ]
    )
    orders = spark.createDataFrame(
        [
            Row(order_id=1001, customer_id=1, order_date="2026-06-01", status="PAID"),
            Row(order_id=1002, customer_id=2, order_date="2026-06-02", status="PAID"),
            Row(order_id=1003, customer_id=3, order_date="2026-06-03", status="SHIPPED"),
            Row(order_id=1004, customer_id=1, order_date="2026-06-04", status="PAID"),
            Row(order_id=1005, customer_id=4, order_date="2026-06-05", status="CANCELLED"),
        ]
    )
    order_items = spark.createDataFrame(
        [
            Row(order_id=1001, product_id=101, quantity=2, unit_price=4.99),
            Row(order_id=1001, product_id=104, quantity=1, unit_price=8.75),
            Row(order_id=1002, product_id=102, quantity=1, unit_price=12.50),
            Row(order_id=1003, product_id=105, quantity=1, unit_price=49.00),
            Row(order_id=1004, product_id=103, quantity=1, unit_price=29.99),
        ]
    )
    shipments = spark.createDataFrame(
        [
            Row(shipment_id=5001, order_id=1001, carrier="UPS", shipped_date="2026-06-02"),
            Row(shipment_id=5002, order_id=1002, carrier="FedEx", shipped_date="2026-06-03"),
            Row(shipment_id=5003, order_id=1003, carrier="UPS", shipped_date="2026-06-04"),
            Row(shipment_id=5004, order_id=1004, carrier="USPS", shipped_date="2026-06-05"),
        ]
    )

    datasets = {
        "customers": customers,
        "products": products,
        "orders": orders,
        "order_items": order_items,
        "shipments": shipments,
    }

    for table_name, dataframe in datasets.items():
        full_name = f"{CATALOG_NAME}.{NAMESPACE}.{table_name}"
        (
            dataframe.writeTo(full_name)
            .using("iceberg")
            .tableProperty("format-version", "2")
            .createOrReplace()
        )
        count = spark.table(full_name).count()
        print(f"Created {full_name} ({count} rows)")

    print("\nTables in demo namespace:")
    spark.sql(f"SHOW TABLES IN {CATALOG_NAME}.{NAMESPACE}").show(truncate=False)

    spark.stop()


if __name__ == "__main__":
    main()
