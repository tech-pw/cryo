/** Maintenance-operation ordering validation, mirroring ``cryo.planner.guardrails``.

The canonical order is "compact, then clean up": optimize first, then manifests, then
expire and orphan removal last -- so a single run reclaims the space its own compaction
freed. ``compute_statistics`` sits right after ``optimize`` because Puffin blobs reference
specific data files.

The backend (``validate_operation_order`` in cryo/planner.guardrails) applies this same
order. Presets are normalised into it on write and again on read, and ``build_plan`` sorts
by it, so the executed sequence is canonical regardless of what a preset stores. The UI
mirrors the validator here so editors see the warning while editing rather than after
saving. Keeping both sides in sync is required; if you change the canonical order on the
backend, update CORRECT_ORDER here too.
*/

const CORRECT_ORDER = [
  "convert_deletes",
  "rewrite_position_deletes",
  "optimize",
  "compute_statistics",
  "optimize_manifests",
  "expire_snapshots",
  "remove_orphan_files"
] as const;

export type MaintenanceOperation = (typeof CORRECT_ORDER)[number];

/** Derived from CORRECT_ORDER so the message can never drift from the tuple. */
const ORDER_PHRASE = `recommended order is ${CORRECT_ORDER.join(" → ")}`;

export const validateOperationOrder = (
  operations: readonly string[]
): string[] => {
  const warnings: string[] = [];
  let lastIndex = -1;
  for (const operation of operations) {
    const index = CORRECT_ORDER.indexOf(operation as MaintenanceOperation);
    if (index === -1) continue;
    if (index < lastIndex) {
      warnings.push(
        `Operation ${operation} should run after earlier maintenance steps; ${ORDER_PHRASE}.`
      );
    }
    lastIndex = Math.max(lastIndex, index);
  }
  return warnings;
};

export const MAINTENANCE_OPERATION_ORDER = CORRECT_ORDER;
