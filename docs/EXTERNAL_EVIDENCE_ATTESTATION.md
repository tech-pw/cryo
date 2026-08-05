# External evidence attestation

The release dossier fails closed unless external evidence is independently
signed and bound to the exact release revision.

An approved verifier creates `cryo.external-evidence/v1`, stores the full raw
artifact at an HTTPS or S3 URI, hashes it, and signs the canonical compact JSON
of every field except `signature` with Ed25519. Cryo release engineering stores
only the verifier's public key as `<verifier>.pem`.
Deployed gates require `environment=production`; interviews, staffing, external
reviews, independent MDR consumers/contributors, and adoption require
`environment=external`.

```bash
python scripts/build_release_dossier.py \
  --version v1.0.0 \
  --revision "$(git rev-parse HEAD)" \
  --external-evidence-dir evidence/manifests \
  --trusted-keys-dir evidence/trusted-keys \
  --output build/release-evidence.json
```

The builder rejects unknown gates, the wrong gate-specific environment, failed results,
unsafe verifier IDs, malformed hashes, altered signatures, and evidence for a
different revision. Counted gates require distinct evidence IDs, including six
interviews, two restore drills, three bounded-automation partners, three case
studies, three MDR consumers, three repeat external contributors, and two
qualifying releases. Duplicate IDs do not increase a count. The registry spans
every Wave 0-6 external graduation gate, including safety campaigns, deployed
faults/SLOs, calibration and holdouts, conformance, canaries, reviews, and
independent adoption.

Public keys establish who signed a manifest, not whether that verifier is
independent or competent. Key approval and verifier independence remain a
human governance decision and must be documented outside the repository.
