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

Report vulnerabilities privately through GitHub: open the repository's **Security** tab and
choose **Report a vulnerability**, or go straight to the [private advisory form][gh-sa]. The
report is visible only to you and the maintainers until a fix is published, and we coordinate
the fix, the advisory and any CVE with you there.

If that form isn't available to you, open a public issue that says only that you have a
security report and asks a maintainer to contact you. Do not include any details of the
vulnerability in it. Do not send findings to a guessed or personal address; no security mailbox
is published. Route non-security support questions according to
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

## Dev token and the platform key

`POST /api/v1/auth/token` self-issues `viewer`, `operator`, or `admin` with no identity check
while the dev token is on. That route stays on unless `CRYO_ENVIRONMENT=prod`. Configuring OIDC
does not turn it off. `CRYO_ALLOW_DEV_TOKEN_IN_PROD` reopens it on `environment=prod`.

The chart default is `CRYO_ENVIRONMENT: dev`. A default `helm install` is not an OIDC install.
A public install must set `prod`, leave the dev-token override false, and set an issuer before
the API is reachable.

`CRYO_SECRET_KEY` defaults to `dev-only-change-me` when unset. HS256 API tokens fall back to that
key. A deployment that omits the secret accepts tokens signed with the published default.

Issued tokens expire in 24 hours. There is no published revocation list.

## Cryptography

Cryo uses [Fernet][fernet] (AES-128-CBC + HMAC-SHA256) from the `cryptography` library for
at-rest secret encryption (`cryo/security/crypto.py`). The platform key is loaded from the
`CRYO_SECRET_KEY` environment variable and is **never** logged or returned by any API. JWTs are
signed with HS256 (default) or RS256 (when OIDC is configured); see `cryo/security/jwt_tokens.py`.

If you find a flaw in our use of these primitives, treat it as a critical-severity report.

[gh-sa]: https://github.com/tech-pw/cryo/security/advisories/new
[fernet]: https://cryptography.io/en/latest/fernet/