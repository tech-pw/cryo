# Ecosystem connector contract

Cryo exposes `cryo.connector/v1` as a conformance contract for externally
maintained source and engine integrations. It deliberately does not discover
or execute arbitrary installed plugins. Operators must explicitly package,
review, configure, and wire a connector.

Each connector publishes a manifest with a stable ID, kind, package,
maintainer, ownership, capabilities, and configuration-schema URL. Source
connectors conform to `SourceCatalog`; engine connectors conform to
`MaintenanceEngine`. Run `check_connector()` in the connector's test suite.

Conformance proves Python protocol shape only. It does not grant a capability
cell, production maturity, security approval, or core-team ownership. A
connector can enter Cryo's capability manifest only after its engine/catalog
semantics, differential fixtures, secret handling, failure behavior, and
maintainer release process have evidence.
