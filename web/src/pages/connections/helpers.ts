/**
 * Small helpers shared by the Connections page and its sub-components.
 */
import { REDACTED_PLACEHOLDER } from "./constants";

export const formatSourceTestSuccessMessage = (namespaceCount: number): string => {
  const noun = namespaceCount === 1 ? "schema" : "schemas";
  return `Connected — found ${namespaceCount} ${noun} in catalog`;
};

/**
 * `REDACTED_PLACEHOLDER` (see `./constants.ts`) must match `cryo/api/redact.py` exactly — the
 * backend serializes every sensitive config key (password/secret/token/etc.) as this literal
 * string before ever sending a Source/Destination config to the browser, and `unredact_merge` on
 * the backend only restores the real stored value when it sees this exact placeholder come back
 * on PATCH. This helper detects it so drawers can show a blank field + "already set" hint instead
 * of leaking the placeholder text into an editable secret input.
 */
export const isRedactedPlaceholder = (value: unknown): value is typeof REDACTED_PLACEHOLDER =>
  value === REDACTED_PLACEHOLDER;

/**
 * Shape returned by `POST /destinations/{id}/test` (see cryo/api/routes.py's `test_destination`).
 * `capacity`/`used`/`available` are counts of concurrent maintenance-operation slots (from
 * `CapacitySnapshot` in cryo/domain/models.py) -- NOT storage bytes or disk capacity.
 */
export interface DestinationCapacityTestResult {
  ok: boolean;
  engine?: string;
  name?: string;
  capacity?: number | null;
  used?: number | null;
  available?: number | null;
  message?: string | null;
}

export const formatDestinationTestSummary = (result: DestinationCapacityTestResult): string => {
  const name = result.name ? `"${result.name}"` : "This engine";
  if (!result.ok) {
    return `${name} isn't ready to run maintenance right now.`;
  }
  if (typeof result.available === "number" && typeof result.capacity === "number") {
    const used = typeof result.used === "number" ? result.used : Math.max(result.capacity - result.available, 0);
    return `${name} has ${result.available} of ${result.capacity} maintenance capacity slots free (${used} in use).`;
  }
  return result.message ?? `${name} was not probed. Do not treat this as reachable.`;
};
