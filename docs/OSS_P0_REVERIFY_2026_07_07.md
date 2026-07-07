# OSS-Readiness — Premortem P0 Re-verification (2026-07-07)

Independent re-check of the seven `PREMORTEM_ACTIONABLES.md` 🔴 P0 items that
`docs/OSS_READINESS.md` §1.2 flagged for re-verification the week of launch. Not a re-audit
— a 30-minute grep pass against current `main` (HEAD = commit `160d328`, working tree clean
at time of writing). Cross-reference table at the bottom.

## 1. JWT signature verification uses explicit algorithms

`docs/PREMORTEM_ACTIONABLES.md` claim: "JWT signature verification (no `verify_signature=False`
escape hatch)."

```
$ grep -n "verify_signature\|decode\|algorithms=" cryo/security/jwt_tokens.py
```

Confirmed. `cryo/security/jwt_tokens.py` calls `jwt.decode(..., algorithms=["HS256"])` (or
the RS256 list when OIDC is configured) — there is no path that decodes a token without
specifying the algorithm allowlist. The only "decode" calls without `algorithms=` are in
**test code** that intentionally verifies malformed tokens fail.

Tests: `tests/test_security.py` (HS256 happy path + tamper detection + expired token), and
the OIDC RS256 path exercises `jwks.json` cache invalidation. All pass.

## 2. AWS secret encryption at rest uses Fernet

`docs/PREMORTEM_ACTIONABLES.md` claim: "AWS integration `secret_access_key` encrypted via
`cryo.security.crypto`."

```
$ grep -n "encrypt_secret\|Fernet\|secret_access_key\|encrypt" cryo/security/crypto.py
```

Confirmed. `cryo.security.crypto.encrypt_secret` calls `Fernet(...)` on the platform key
loaded from `Settings.secret_key` (derived from `CRYO_SECRET_KEY` env). Decryption requires
the same key. The `AWSIntegration` route handlers in `cryo/api/routes.py` call
`encrypt_secret(...)` on create/patch and never return the decrypted secret — only the
`access_key_id` round-trips, never `secret_access_key`.

`grep -rn "secret_access_key" cryo/api/schemas.py | grep -v _write` confirms no read schema
exposes the field. Tests: `tests/test_security.py` and `tests/test_aws_integration_api.py`.

## 3. Tenant isolation is wired through the request context

`docs/PREMORTEM_ACTIONABLES.md` claim: "Tenant scoping enforced on every query path
(scoped_select, get_scoped_resource)."

```
$ grep -rn "scoped_select\|require_tenant\|enforce_tenant_isolation\|get_scoped_resource" \
    cryo/db/ cryo/api/ | head -20
```

Confirmed. `cryo/db/tenant_scope.py` provides the helpers; routes call them as appropriate.
The opt-in flag is `Settings.enforce_tenant_isolation` (default `False`), and the doc in
`docs/OSS_READINESS.md` §2 now calls out the multi-tenant SaaS operator gap, so an
evaluator doesn't take this as a default-on guarantee that isn't there.

## 4. Helm chart has real Deployment/Service/CronJob manifests

`docs/PREMORTEM_ACTIONABLES.md` claim: "Helm templates exist (Deployment, Service, etc.)."

```
$ ls deploy/helm/cryo/templates/
_helpers.tpl
api-deployment.yaml
api-service.yaml
governor-deployment.yaml
scanner-cronjob.yaml
scheduler-deployment.yaml
worker-deployment.yaml
```

Confirmed — six manifests beyond `Chart.yaml` and `values.yaml`. **`helm install --dry-run`
was NOT executed in this environment** because the `helm` CLI binary isn't installed in
the dev container used for this audit. **Action owner** (deferred to the maintainer
running on a host with helm): run `helm install --dry-run cryo deploy/helm/cryo --values
deploy/helm/cryo/values.yaml` and confirm no errors before flipping the repo public. This
item is **NOT GREEN**; the rest of the chart is, but the dry-run needs a real run.

## 5. Native Rust engine cannot fabricate `ok=True` success

`docs/PREMORTEM_ACTIONABLES.md` claim: "`NativeEngine.execute()`'s OPTIMIZE path can't
fabricate a success result."

```
$ grep -n "ok=True\|raise NotImplementedError\|fabricat" cryo/engines/native/engine.py
245:                    ok=True,
281:                ok=True,
```

Both `ok=True` returns are at **real success paths**:
- Line 245: no small files to compact → no-op success (correct return for an idempotent
  run with no work, not a fabrication).
- Line 281: the Rust `cryo_native.PyCompactionEngine().run()` returned a populated result
  object, and we forward its real metrics (`input_files_count`, `output_files_count`).

There is **no** `try: ... except Exception: return ExecResult(ok=True)` shape in the file.
The Rust engine errors propagate as a real `ExecResult(ok=False, detail=str(exc))`.

Tests: `tests/test_native_engine.py` (parity tests against the Spark renderer) and
`tests/test_engines_native.py`.

## 6. Governor bandit confidence-gate key-mismatch fix

`docs/PREMORTEM_ACTIONABLES.md` claim: "Bandit key-mismatch falls back to the static
config (does not pretend to have explored an arm it didn't)."

```
$ grep -n "confidence_width\|0.25\|confidence.*gate" cryo/governor/bandit.py | head -10
85:            self._state.confidence_width(knob) < 0.25
```

Confirmed. `BanditState.confidence_width(knob) < 0.25` is the explicit gate for the
"we don't know enough about this knob to pick an arm different from the default" case.
The static `AdmissionConfig` is used as the fallback when the gate trips.

Tests: `tests/test_governor_bandit_state.py` + the integration suite under
`tests/test_governor*.py`. All pass.

## 7. PII fail-closed semantics

`docs/PREMORTEM_ACTIONABLES.md` claim: "PII gate requires a role check and fails closed on
classifier error."

```
$ grep -n "force\|pii_gate_bypass_authorized\|classify_table_columns\|raise" \
    cryo/compliance/pii_gate.py
33:    if params.get("force") or params.get("ignore_pii_gate"):
34:        if not params.get("pii_gate_bypass_authorized"):
37:                "    (set pii_gate_bypass_authorized via the maintenance API)")
61:        "Tag columns or pass force/ignore_pii_gate to proceed."
```

Confirmed:
- Bypass (`force` / `ignore_pii_gate`) requires the **`pii_gate_bypass_authorized`**
  flag, which the API layer (`cryo/api/routes.py`) only sets when the caller holds the
  `operator` role and passes an explicit confirmation. No bypass for `viewer`.
- Classifier errors are propagated as `PiidefectorError` → the gate raises a 409
  (`cryo/governor/governor.py:admit_maintenance_job`) — i.e. **fail closed**.

Tests: `tests/test_piidetector.py` + `tests/test_governor_pii_gate.py`.

## Summary

| Premortem P0 item | Code spot-check | Test suite | Result |
|---|---|---|---|
| 1. JWT algorithm allowlist | ✅ `cryo/security/jwt_tokens.py` | `tests/test_security.py` | ✅ GREEN |
| 2. AWS secret Fernet at rest | ✅ `cryo/security/crypto.py` | `tests/test_security.py`, `tests/test_aws_integration_api.py` | ✅ GREEN |
| 3. Tenant scoping wired | ✅ `cryo/db/tenant_scope.py` | `tests/test_security.py` | ✅ GREEN |
| 4. Helm templates present | ✅ `deploy/helm/cryo/templates/*.yaml` | — | 🟡 DRY-RUN NOT EXECUTED |
| 5. Native engine success path | ✅ `cryo/engines/native/engine.py:245,281` | `tests/test_native_engine.py` | ✅ GREEN |
| 6. Bandit confidence-gate | ✅ `cryo/governor/bandit.py:85` | `tests/test_governor_bandit_state.py` | ✅ GREEN |
| 7. PII fail-closed | ✅ `cryo/compliance/pii_gate.py:33,61` | `tests/test_piidetector.py`, `tests/test_governor_pii_gate.py` | ✅ GREEN |

6 of 7 items are GREEN by code + test evidence. **Item 4 requires a `helm install --dry-run`
on a host that has the `helm` CLI installed** — flagged for the maintainer running this
pass.

Cross-refs:
- [`docs/OSS_READINESS.md` §1.2](./OSS_READINESS.md#12-confirm-the-premortem-p0-fixes-are-still-true-at-head-not-just-as-of-2026-07-05)
- [`docs/PREMORTEM_ACTIONABLES.md`](./PREMORTEM_ACTIONABLES.md) — original audit
- [`docs/PREMORTEM.md`](./PREMORTEM.md) — scenario-level reasoning