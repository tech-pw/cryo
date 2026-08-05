# Safe maintenance rollout

Destinations may opt into automatic cohort halting through their existing `config` JSON:

```json
{
  "rollout_policy": {
    "enabled": true,
    "phase": "canary",
    "window_minutes": 60,
    "min_attempts": 20,
    "max_failure_rate": 0.05,
    "max_unknown_effect_rate": 0.01
  }
}
```

The leader Scheduler evaluates enabled policies once per minute. It observes only terminal durable
attempts and durable effect receipts in the declared window. Before `min_attempts`, it records an
`observing` decision and cannot pass or halt the cohort.

When either budget is exceeded, the controller writes the existing durable destination pause with
actor `rollout-safety-controller` and an exact reason. The pause is audited and enforced again at
the final pre-effect guard. It never auto-resumes: an operator must inspect the cohort, reconcile
unknown effects, and explicitly resume the destination.

`cryo_rollout_decisions_total{phase,decision}` exposes observing, healthy, halt, and invalid-policy
decisions. A forced regression test proves policy math locally; Wave 2 still requires a deployed
regression to halt inside the declared budget and a retained rollback exercise.
