import { describe, expect, it } from "vitest";

import { MAINTENANCE_OPERATION_ORDER, validateOperationOrder } from "./operationOrder";

describe("validateOperationOrder", () => {
  it("returns no warnings for canonical order", () => {
    expect(validateOperationOrder([...MAINTENANCE_OPERATION_ORDER])).toEqual([]);
  });

  it("returns no warnings for a subset in canonical order", () => {
    expect(validateOperationOrder(["optimize", "expire_snapshots"])).toEqual([]);
  });

  it("returns no warnings for an empty selection", () => {
    expect(validateOperationOrder([])).toEqual([]);
  });

  it("flags expire_snapshots appearing before optimize", () => {
    const warnings = validateOperationOrder(["expire_snapshots", "optimize"]);
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toMatch(/optimize should run after earlier/);
  });

  it("flags remove_orphan_files appearing before expire_snapshots", () => {
    const warnings = validateOperationOrder(["remove_orphan_files", "expire_snapshots"]);
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toMatch(/expire_snapshots should run after earlier/);
  });

  it("flags optimize appearing after optimize_manifests", () => {
    const warnings = validateOperationOrder(["optimize_manifests", "optimize"]);
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toMatch(/optimize should run after earlier/);
  });

  it("ignores unknown operations without raising", () => {
    expect(validateOperationOrder(["unknown_op", "optimize"])).toEqual([]);
  });
});
