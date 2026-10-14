import { describe, expect, it } from "vitest";
import type { DiscoveredBucket } from "../../api/client";
import { mergeBuckets } from "./useStorageDiscovery";

const bucket = (over: Partial<DiscoveredBucket> & { name: string }): DiscoveredBucket => ({
  tables: 0,
  schemas: 0,
  access: "unknown",
  access_detail: null,
  ...over
});

describe("mergeBuckets", () => {
  it("adds counts when a bucket appears in more than one slice", () => {
    // Each slice covers different databases, so the same bucket legitimately recurs --
    // totals must accumulate rather than the last slice overwriting earlier ones.
    const first = mergeBuckets(new Map(), [bucket({ name: "silver", tables: 10, schemas: 2 })]);
    const second = mergeBuckets(first, [bucket({ name: "silver", tables: 5, schemas: 1 })]);

    expect(second.get("silver")).toMatchObject({ tables: 15, schemas: 3 });
  });

  it("keeps a known access status when a later slice reports unknown", () => {
    // Only the slice that first surfaces a bucket probes it; later slices send "unknown",
    // which must not erase the answer already shown to the operator.
    const first = mergeBuckets(new Map(), [
      bucket({ name: "locked", tables: 1, access: "denied", access_detail: "no read" })
    ]);
    const second = mergeBuckets(first, [bucket({ name: "locked", tables: 2 })]);

    expect(second.get("locked")).toMatchObject({ access: "denied", access_detail: "no read" });
  });

  it("upgrades unknown access once a slice reports a real status", () => {
    const first = mergeBuckets(new Map(), [bucket({ name: "gold", tables: 1 })]);
    const second = mergeBuckets(first, [bucket({ name: "gold", tables: 1, access: "readable" })]);

    expect(second.get("gold")?.access).toBe("readable");
  });

  it("carries new buckets through untouched", () => {
    const merged = mergeBuckets(new Map(), [
      bucket({ name: "bronze", tables: 7, schemas: 3, access: "readable" })
    ]);

    expect(merged.get("bronze")).toMatchObject({ tables: 7, schemas: 3, access: "readable" });
  });
});
