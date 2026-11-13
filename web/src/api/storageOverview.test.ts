import { describe, expect, it } from "vitest";
import { storageFixture } from "../pages/home/storageFixture";
import { isStorageOverview } from "./storageOverview";

describe("isStorageOverview", () => {
  it("accepts a complete payload", () => {
    expect(isStorageOverview(storageFixture())).toBe(true);
    expect(isStorageOverview(storageFixture({ versioning_findings: null }))).toBe(true);
  });

  it("rejects stubs, error bodies and payloads missing a section", () => {
    expect(isStorageOverview([])).toBe(false);
    expect(isStorageOverview(null)).toBe(false);
    expect(isStorageOverview({ detail: "Not Found" })).toBe(false);
    const { freed_24h: _freed, ...noFreed } = storageFixture();
    expect(isStorageOverview(noFreed)).toBe(false);
    expect(isStorageOverview({ ...storageFixture(), breakdown: { total_bytes: 1 } })).toBe(false);
    expect(isStorageOverview({ ...storageFixture(), trend: null })).toBe(false);
  });
});
