import { describe, expect, it } from "vitest";

import { validateOperationOrder } from "./operationOrder";

describe("validateOperationOrder", () => {
  it("returns no warnings for canonical order", () => {
    expect(
      validateOperationOrder([
        "expire_snapshots",
        "remove_orphan_files",
        "optimize",
        "optimize_manifests"
      ])
    ).toEqual([]);
  });

  it("returns no warnings for a subset in canonical order", () => {
    expect(validateOperationOrder(["expire_snapshots", "optimize"])).toEqual([]);
  });

  it("returns no warnings for an empty selection", () => {
    expect(validateOperationOrder([])).toEqual([]);
  });

  it("flags expire_snapshots appearing after optimize", () => {
    const warnings = validateOperationOrder(["optimize", "expire_snapshots"]);
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