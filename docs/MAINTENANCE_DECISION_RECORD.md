# Maintenance Decision Record v1

The Maintenance Decision Record (MDR) is Cryo's stable, machine-readable proof of why maintenance
was authorized, what immutable plan was bound, which effects were observed, and what outcome was
recorded. It is an export contract, not an execution protocol.

## Export

An operator can retrieve a record from:

```text
GET /api/v1/evidence/maintenance-decisions/{intent_id}
```

The response conforms to
[`schemas/maintenance-decision-record-v1.schema.json`](schemas/maintenance-decision-record-v1.schema.json).
The endpoint exports only Cryo-owned durable state and does not accept operator-supplied evidence.

## Privacy and integrity

MDR v1 is redacted by default:

- actor identity and table reference are replaced by SHA-256 digests;
- partition values in the canonical plan are replaced by SHA-256 digests;
- destination configuration, query text, SQL, audit detail, worker identity, effect submissions,
  idempotency keys, and unbounded engine result payloads are omitted;
- effect results expose only bounded numeric maintenance metrics;
- error payloads expose an error class, never an error message.

`record_id` binds the intent and immutable plan. `integrity.record_sha256` hashes the complete record
before the `integrity` member is attached, using sorted compact JSON. Consumers should retain the
original JSON document and reject records whose schema version is unsupported.

## Compatibility policy

- `cryo.maintenance-decision-record/v1` is stable.
- New optional members may be added within v1. Consumers must ignore unknown members.
- Existing members, meanings, privacy guarantees, and required fields will not change within v1.
- Removing or renaming a field, widening disclosed data, or changing integrity semantics requires
  a new major schema version and a parallel compatibility window.
- Cryo keeps v1 export support for at least two minor releases after a successor becomes GA.

The schema and exporter tests are release evidence. Production references and three independent
consumers remain external graduation gates and are not inferred from repository coverage.
