from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from cryo.benchmarks.fault_matrix import fault_matrix_report, validate_fault_matrix
from cryo.benchmarks.fleet import CustomerFleetSpec, generate_customer_fleet


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate reproducible CryoBench evidence inputs.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tables", type=int, default=12_938)
    parser.add_argument("--tenants", type=int, default=12)
    parser.add_argument("--sources", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260804)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = create_parser().parse_args(argv)
    validate_fault_matrix()
    fleet = generate_customer_fleet(
        CustomerFleetSpec(
            table_count=args.tables,
            tenant_count=args.tenants,
            source_count=args.sources,
            seed=args.seed,
        )
    )
    document: dict[str, Any] = {
        "schema_version": "cryobench/v1",
        "fleet": fleet.to_dict(),
        "fault_matrix": fault_matrix_report(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
