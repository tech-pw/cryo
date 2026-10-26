# Cryo operator skills

Procedures for installing and debugging Cryo. Invoke one with `/<name>`. They explain and classify. They do not install, upgrade, or run maintenance.

| Skill | Use when |
|---|---|
| [cryo-install](./cryo-install/SKILL.md) | Rendering or checking a Helm install. Never installs. |
| [cryo-run-triage](./cryo-run-triage/SKILL.md) | A maintenance run failed and you have the log or pod status. |
| [cryo-scan-health](./cryo-scan-health/SKILL.md) | A scan is partial, a table is broken, or freshness looks wrong. |
| [cryo-queue-stuck](./cryo-queue-stuck/SKILL.md) | Jobs are queued, deferred, or admitted and not moving. |
| [cryo-config-explain](./cryo-config-explain/SKILL.md) | A table was skipped, the engine looks wrong, or a grade needs explaining. |

The read-only debug agent is [../../.agents/agents/cryo-debug.md](../../.agents/agents/cryo-debug.md). It reads these procedures. It does not preload them: `.agents/skills/` is gitignored local scratch, so a committed skill there would never reach the repo.

These files help a session opened in this repo. An operator who only installed the chart and never cloned the repo does not get them.

Shared boundary, from [docs/SUPPORT_POLICY.md](../SUPPORT_POLICY.md) and [docs/AI_ASSISTANT.md](../AI_ASSISTANT.md): no maintenance execution, no `force=true`, and `confirm=true` does not execute. Maintenance order is owned by `CORRECT_ORDER` in `cryo/planner/guardrails.py`. Orphan-file warnings stay in [docs/WORKER_STEP_LEDGER.md](../WORKER_STEP_LEDGER.md). A skill may point at that file. It must not copy it.

If a skill and the code disagree, the code wins.
