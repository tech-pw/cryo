# Deployed rewrite conformance evidence

Cryo keeps PTO canaries disabled until an isolated production-shaped experiment proves that Trino
and Spark preserve the same rows, schema, partition specification, and bounded touched scope. This
document describes the evidence collection boundary; it does not authorize a rewrite.

## Trust boundary

The ingestion endpoint is:

```text
POST /api/v1/tables/{table_id}/layout-conformance/deployed
```

It accepts only a JWT containing the dedicated `evidence_collector` role. That role cannot read or
operate Cryo, and viewer, operator, and admin roles cannot submit collector evidence. Issue this
identity only to the isolated collector workload through the deployment's workload-identity
mechanism. Do not issue long-lived user tokens for it.

The ordinary operator endpoint remains synthetic-only and can never set
`qualifies_production_gate=true`.

## Collection procedure

1. Clone one source snapshot into isolated Trino and Spark experiment tables.
2. Record the source snapshot ID and SHA-256 of its Iceberg metadata location.
3. Execute the exact differential-oracle rewrite scope independently in each engine.
4. Compute deterministic SHA-256 values for the row multiset, logical schema, partition spec, every
   touched partition/file scope, and each output metadata location. Never put table paths, row
   values, SQL, or credentials in the payload.
5. Record distinct positive Trino and Spark output snapshot IDs.
6. Copy the current `pto_params_sha256` and `rewrite_scope_sha256` from Cryo's differential dossier.
7. Set `provenance` to `deployed_collector/v1`, `environment` to `production`, and `collected_at` to
   a timezone-aware current timestamp.
8. Bind the payload to the collector identity:

   ```bash
   python scripts/build_deployed_conformance_payload.py \
     --input observed.json \
     --output attested.json \
     --collector-id 'spiffe://example/cryo/conformance-collector'
   ```

9. Submit `attested.json` within 15 minutes using the collector workload token.
10. Confirm the response is conformant and `qualifies_production_gate=true`; retain the audit event
    and exact payload digest in the release evidence store.

The server recomputes the attestation, binds it to the authenticated collector subject, rejects
stale/future observations, rejects recommendation or scope hash drift, and requires real snapshot
and metadata identities. The record stores only collector/deployment hashes and bounded evidence.

## Failure handling

Any mismatch remains a blocker. Do not edit hashes or widen scope to make a run pass. Preserve the
failed evidence, pause that candidate, inspect row/spec/scope differences, and rerun from a fresh
source snapshot after correcting the underlying engine or renderer behavior.

This mechanism makes genuine evidence ingestible; it does not create production evidence by itself.
