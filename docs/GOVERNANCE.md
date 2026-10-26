# Cryo — Open Source Governance (pre-v1)

Cryo is licensed under the [Apache License 2.0](../LICENSE). The canonical
repository is [github.com/tech-pw/cryo](https://github.com/tech-pw/cryo). That
repository is private until the public flip.

This document describes the intended governance once the repository is public. It does **not** establish a
full Apache-style PMC, CLA, or foundation structure — that overhead is deferred until external
contributor volume justifies it (see [`research-and-roadmap.md`](./research-and-roadmap.md) Part 5).

## How decisions are made today

| Area | Process |
|------|---------|
| **Code changes** | GitHub pull requests; maintainer review + CI green |
| **Roadmap / architecture** | `plan.md`, `hld.md`, `lld.md`, `docs/EXECUTION_PLAN.md` |
| **Releases** | Tag on `main`; release notes in GitHub Releases |
| **Security issues** | Private disclosure to maintainers (GitHub Security Advisories when enabled) |

## Contributing

Read [CONTRIBUTING.md](../CONTRIBUTING.md) for setup, commit conventions, and PR expectations.

## Maintainers

The current maintainers are listed in [`MAINTAINERS.md`](../MAINTAINERS.md). Review ownership
per path is in [`.github/CODEOWNERS`](../.github/CODEOWNERS); keep the two in sync.

## Maintainer responsibilities

- Keep `main` deployable (CI: lint, typecheck, tests).
- Review PRs within a reasonable window; prefer small, focused changes.
- Update `docs/CAPABILITIES.md` when behavior or contracts change.
- Do not merge breaking API/schema changes without a migration and doc update.

## When to formalize further

Consider Apache-style governance (PMC, CLA, trademark policy) when **all** of these are true:

- Sustained external contributor activity (multiple active non-maintainer contributors).
- Multiple organizations depending on Cryo in production.
- Need for neutral trademark / release authority beyond a single org.

Until then, GitHub-first governance under `tech-pw` is sufficient.

## License

All contributions are licensed under Apache 2.0. See `LICENSE` and the contribution section in
`CONTRIBUTING.md`.
