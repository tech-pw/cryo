from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from cryo.optimizer.conformance import (
    DeployedRewriteEvidence,
    RewriteConformanceFixture,
    RewriteOutputSnapshot,
    deployed_attestation_sha256,
)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bind collector observations into a deployed conformance API payload."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collector-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    source = json.loads(args.input.read_text())
    evidence = _evidence_from_payload(source)
    attested = replace(
        evidence,
        attestation_sha256=deployed_attestation_sha256(
            evidence,
            collector_id=args.collector_id,
        ),
    )
    output = {
        **source,
        "attestation_sha256": attested.attestation_sha256,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    return 0


def _evidence_from_payload(payload: dict[str, Any]) -> DeployedRewriteEvidence:
    fixture = RewriteConformanceFixture(
        fixture_id=str(payload["fixture_id"]),
        provenance=str(payload["provenance"]),
        baseline=_snapshot(payload["baseline"]),
        trino=_snapshot(payload["trino"]),
        spark=_snapshot(payload["spark"]),
        allowed_scope_sha256=tuple(payload["allowed_scope_sha256"]),
    )
    return DeployedRewriteEvidence(
        fixture=fixture,
        collected_at=datetime.fromisoformat(str(payload["collected_at"])),
        deployment_id=str(payload["deployment_id"]),
        environment=str(payload["environment"]),
        pto_params_sha256=str(payload["pto_params_sha256"]),
        rewrite_scope_sha256=str(payload["rewrite_scope_sha256"]),
        attestation_sha256="",
    )


def _snapshot(payload: dict[str, Any]) -> RewriteOutputSnapshot:
    return RewriteOutputSnapshot(
        engine=str(payload["engine"]),
        row_count=int(payload["row_count"]),
        row_multiset_sha256=str(payload["row_multiset_sha256"]),
        schema_sha256=str(payload["schema_sha256"]),
        partition_spec_sha256=str(payload["partition_spec_sha256"]),
        touched_scope_sha256=tuple(payload["touched_scope_sha256"]),
        snapshot_id=int(payload["snapshot_id"]),
        metadata_location_sha256=str(payload["metadata_location_sha256"]),
    )


if __name__ == "__main__":
    raise SystemExit(main())
