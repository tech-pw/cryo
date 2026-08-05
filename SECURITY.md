# Security Policy

Cryo runs destructive operations against production data (snapshot expiry, orphan-file removal,
manifest rewrite, partition rewrite via OPTIMIZE) and stores AWS credentials, OIDC client secrets,
and database connection strings. If you have found a vulnerability, please follow the disclosure
process below — **do not file a public GitHub issue for security-sensitive findings.**

## Supported versions

Only the latest minor release line on `main` receives security fixes. Older revisions are not
patched. Pin your deployment to a tagged release when you need a stable target.

| Version line | Supported |
|---|---|
| Latest `main` | ✅ |
| Tagged releases (`v0.x.y`) | ✅ for the most recent tag only |
| Anything older | ❌ |

## Reporting a vulnerability

**Preferred:** Open a [GitHub Security Advisory][gh-sa] against this repository. Advisories are
visible only to the maintainer team until a fix is published.

No fallback security mailbox is currently published. Use the private advisory
channel rather than sending sensitive findings to guessed or personal
addresses. Route non-security support questions according to
[`docs/SUPPORT_POLICY.md`](./docs/SUPPORT_POLICY.md).

When reporting, please include:

1. The affected component (e.g., `cryo/engines/trino/sql.py`, `cryo/security/crypto.py`, a
   specific Helm template).
2. Reproduction steps — concrete commands, sample input, observed vs. expected behavior.
3. Impact assessment — what an attacker could achieve, and under what conditions (auth required?
   network reachable? local?).
4. Any known workarounds.

We aim to acknowledge new reports within **5 business days** and to publish a fix or mitigation
within **30 days** for high-severity issues. Lower-severity reports may take longer; you will get
a status update either way.

## Out of scope

The following are **not** security vulnerabilities and should be filed as regular GitHub
issues (after triage, not before):

- Vulnerabilities in dependencies that already have an upstream fix in a newer release — please
  open a PR bumping the pin instead.
- Theoretical concerns without a reproducible attack path — open an Issue for discussion.
- Reports about scan noise, false positives, or alert spam.

## Cryptography

Cryo uses [Fernet][fernet] (AES-128-CBC + HMAC-SHA256) from the `cryptography` library for
at-rest secret encryption (`cryo/security/crypto.py`). The platform key is loaded from the
`CRYO_SECRET_KEY` environment variable and is **never** logged or returned by any API. JWTs are
signed with HS256 (default) or RS256 (when OIDC is configured); see `cryo/security/jwt_tokens.py`.

If you find a flaw in our use of these primitives, treat it as a critical-severity report.

[gh-sa]: https://github.com/shelf-project/Cryo/security/advisories/new
[fernet]: https://cryptography.io/en/latest/fernet/