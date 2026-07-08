/** Maintenance-operation ordering validation, mirroring ``cryo.planner.guardrails``.

The backend (``validate_operation_order`` in cryo/planner.guardrails) enforces
the canonical expire → orphan → optimize → manifests order on every
/maintenance/run and /maintenance/preview call -- out-of-order sequences get
a 400. The UI mirrors that same validator here so editors see the warning
*before* submitting instead of after a server-side rejection. Keeping both
sides in sync is required; if you change the canonical order on the backend,
update CORRECT_ORDER here too.
*/

const CORRECT_ORDER = [
  "expire_snapshots",
  "remove_orphan_files",
  "optimize",
  "optimize_manifests"
] as const;

export type MaintenanceOperation = (typeof CORRECT_ORDER)[number];

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
        `Operation ${operation} should run after earlier maintenance steps; recommended order is expire → orphan → optimize → manifests.`
      );
    }
    lastIndex = Math.max(lastIndex, index);
  }
  return warnings;
};

export const MAINTENANCE_OPERATION_ORDER = CORRECT_ORDER;