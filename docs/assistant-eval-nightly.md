# Ask Cryo eval nightly job

Not enabled. Owner decision required before any staging run against a real model.

Proposed schedule: weekdays 02:30 UTC, staging only.

- Command: `python -m pytest tests/test_assistant_eval_cases.py -q`
- Provider: the staging model named in Connections. Do not use production.
- Gate: the corpus in `tests/test_assistant_eval_cases.py` (200+ cases, held-out subset, 50+ action cases).
- On failure: write a report. Do not change `assistant_enabled` or `assistant_writes_enabled`.
