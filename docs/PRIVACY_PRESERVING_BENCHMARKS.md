# Privacy-preserving fleet benchmarks

Cryo can derive an optional aggregate report from its local, lineage-complete
maintenance outcomes. Nothing is transmitted automatically.

The v1 report:

- groups only by engine, operation, and a coarse table-size bucket;
- requires at least 10 distinct tables and 20 outcomes in every published
  cohort;
- suppresses smaller cohorts;
- publishes rounded medians rather than row-level values;
- excludes table, job, intent, plan, tenant, actor, destination, and timestamp
  identifiers.

This is k-anonymous aggregation, **not differential privacy**. It does not
protect against every differencing or auxiliary-information attack. Operators
should export only a fixed, reviewed reporting window and avoid repeatedly
publishing overlapping small populations. Future cross-organization learning
requires privacy review and a stronger threat model before admission.
