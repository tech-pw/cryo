# Cryo — Open Source Governance (pre-v1)

Cryo is open source under the [Apache License 2.0](../LICENSE) at
[github.com/shelf-project/Cryo](https://github.com/shelf-project/Cryo).

This document describes **current** governance. It intentionally does **not** establish a
full Apache-style PMC, CLA, or foundation structure — that overhead is deferred until external
contributor volume justifies it (see `docs/PERFORMANCE_AND_OPENNESS.md` Part 4).

## How decisions are made today

| Area | Process |
|------|---------|
| **Code changes** | GitHub pull requests; maintainer review + CI green |
| **Roadmap / architecture** | `plan.md`, `hld.md`, `lld.md`, `docs/EXECUTION_PLAN.md` |
| **Releases** | Tag on `main`; release notes in GitHub Releases |
| **Security issues** | Private disclosure to maintainers (GitHub Security Advisories when enabled) |

## Contributing

Read [CONTRIBUTING.md](../CONTRIBUTING.md) for setup, commit conventions, and PR expectations.

## Maintainer responsibilities

- Keep `main` deployable (CI: lint, typecheck, tests).
- Review PRs within a reasonable window; prefer small, focused changes.
- Update `docs/current-state.md` when behavior or contracts change.
- Do not merge breaking API/schema changes without a migration and doc update.

## When to formalize further

Consider Apache-style governance (PMC, CLA, trademark policy) when **all** of these are true:

- Sustained external contributor activity (multiple active non-maintainer contributors).
- Multiple organizations depending on Cryo in production.
- Need for neutral trademark / release authority beyond a single org.

Until then, GitHub-first governance under `shelf-project` is sufficient.

## License

All contributions are licensed under Apache 2.0. See `LICENSE` and the contribution section in
`CONTRIBUTING.md`.
