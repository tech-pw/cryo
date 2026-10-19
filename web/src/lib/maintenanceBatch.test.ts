import { beforeEach, describe, expect, it } from "vitest";
import { MAINTENANCE_OPERATION_ORDER } from "./operationOrder";
import {
  addMatchingFilter,
  addTables,
  batchCapMessage,
  excludeTable,
  loadDraft,
  MAINTENANCE_BATCH_CAP,
  maintenanceBatchStorageKey,
  orderedRecipeOperations,
  type BatchIdentity,
  type BatchStorage,
  type BatchTableRef
} from "./maintenanceBatch";

const identity: BatchIdentity = {
  installationId: "install-1",
  principalId: "user-1",
  tenantId: "tenant-1"
};

const otherIdentity: BatchIdentity = {
  installationId: "install-1",
  principalId: "user-2",
  tenantId: "tenant-1"
};

const table = (id: string): BatchTableRef => ({
  id,
  label: `catalog › schema › ${id}`
});

describe("maintenanceBatch", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("scopes a versioned storage key by installation, principal and tenant", () => {
    expect(maintenanceBatchStorageKey(identity)).toBe(
      "cryo.maintenance-batch.v1:install-1:user-1:tenant-1"
    );
    expect(maintenanceBatchStorageKey(otherIdentity)).not.toBe(maintenanceBatchStorageKey(identity));
  });

  it("dedupes tables by UUID", () => {
    const first = addTables(identity, [table("a"), table("a"), { id: "a", label: "other" }]);
    expect(first.ok).toBe(true);
    if (first.ok) expect(first.added).toBe(1);
    const second = addTables(identity, [table("a"), table("b")]);
    expect(second.ok).toBe(true);
    if (second.ok) {
      expect(second.added).toBe(1);
      expect(second.alreadyIn).toBe(1);
    }
    expect(loadDraft(identity).tables.map((row) => row.id)).toEqual(["a", "b"]);
    expect(loadDraft(identity).tables[0]?.label).toBe("catalog › schema › a");
  });

  it("rejects an addition that would exceed the cap without truncating", () => {
    const filled = Array.from({ length: MAINTENANCE_BATCH_CAP }, (_, index) => table(`t${index}`));
    expect(addTables(identity, filled).ok).toBe(true);
    const overflow = addTables(identity, [table("t0"), table("overflow")]);
    expect(overflow.ok).toBe(false);
    if (!overflow.ok) {
      expect(overflow.code).toBe("cap_exceeded");
      expect(overflow.message).toBe(batchCapMessage());
    }
    const stored = loadDraft(identity);
    expect(stored.tables).toHaveLength(MAINTENANCE_BATCH_CAP);
    expect(stored.tables.some((row) => row.id === "overflow")).toBe(false);

    sessionStorage.clear();
    const tooMany = Array.from({ length: MAINTENANCE_BATCH_CAP + 1 }, (_, index) => table(`n${index}`));
    expect(addTables(identity, tooMany).ok).toBe(false);
    expect(loadDraft(identity).tables).toEqual([]);
  });

  it("stores an all-matching filter receipt and never the current page ids", () => {
    const pageIds = ["page-1", "page-2", "page-3"];
    const result = addMatchingFilter(identity, {
      filter: { field: "grade", op: "=", value: "F" },
      sourceId: "source-1",
      view: "freshness",
      declaredCount: 3,
      label: "All matching",
      pageIds
    });
    expect(result.ok).toBe(true);
    const draft = loadDraft(identity);
    expect(draft.tables).toEqual([]);
    expect(draft.receipts).toHaveLength(1);
    expect(draft.receipts[0]).toMatchObject({
      kind: "filter_receipt",
      status: "pending",
      sourceId: "source-1",
      view: "freshness",
      declaredCount: 3
    });
    const raw = sessionStorage.getItem(maintenanceBatchStorageKey(identity)) ?? "";
    expect(raw).not.toContain("page-1");
    expect(raw).not.toContain("pageIds");
    expect(JSON.stringify(draft)).not.toContain("page-1");
  });

  it("rejects an all-matching addition over the cap without storing page ids", () => {
    const pageIds = Array.from({ length: 12 }, (_, index) => `page-${index}`);
    const result = addMatchingFilter(identity, {
      filter: { all: [] },
      declaredCount: MAINTENANCE_BATCH_CAP + 1,
      pageIds
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toBe(batchCapMessage());
    expect(loadDraft(identity).receipts).toEqual([]);
    expect(sessionStorage.getItem(maintenanceBatchStorageKey(identity)) ?? "").not.toContain("page-0");
  });

  it("clears the draft when installation, principal or tenant changes", () => {
    addTables(identity, [table("a")]);
    const previousKey = maintenanceBatchStorageKey(identity);
    expect(sessionStorage.getItem(previousKey)).toBeTruthy();
    expect(loadDraft(otherIdentity).tables).toEqual([]);
    expect(sessionStorage.getItem(previousKey)).toBeNull();
    expect(loadDraft(identity).tables).toEqual([]);
  });

  it("keeps an exclusion until the table is explicitly re-added", () => {
    addTables(identity, [table("a"), table("b")]);
    excludeTable(identity, "a");
    expect(loadDraft(identity).tables.map((row) => row.id)).toEqual(["b"]);
    expect(loadDraft(identity).exclusions.map((entry) => entry.tableId)).toEqual(["a"]);

    addMatchingFilter(identity, {
      filter: { field: "grade", op: "=", value: "F" },
      declaredCount: 2,
      pageIds: ["a", "b"]
    });
    expect(loadDraft(identity).exclusions.map((entry) => entry.tableId)).toEqual(["a"]);
    expect(loadDraft(identity).tables.map((row) => row.id)).toEqual(["b"]);
    expect(sessionStorage.getItem(maintenanceBatchStorageKey(identity)) ?? "").not.toContain('"pageIds"');

    addTables(identity, [table("a")]);
    expect(loadDraft(identity).tables.map((row) => row.id)).toEqual(["b", "a"]);
    expect(loadDraft(identity).exclusions).toEqual([]);
  });

  it("resets malformed storage without throwing", () => {
    const key = maintenanceBatchStorageKey(identity);
    sessionStorage.setItem(key, "{not json");
    expect(loadDraft(identity).tables).toEqual([]);
    expect(() => JSON.parse(sessionStorage.getItem(key) ?? "")).not.toThrow();

    sessionStorage.setItem(
      key,
      JSON.stringify({
        version: 1,
        identity: { ...identity, principalId: "someone-else" },
        revision: 2,
        tables: [table("foreign-row")],
        receipts: [],
        exclusions: [],
        recipe: "compact",
        updatedAt: "2026-01-01T00:00:00.000Z"
      })
    );
    const reset = loadDraft(identity);
    expect(reset.tables).toEqual([]);
    expect(JSON.stringify(reset)).not.toContain("foreign-row");
  });

  it("swallows storage failures", () => {
    const storage: BatchStorage = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
      removeItem: () => {
        throw new Error("blocked");
      }
    };
    expect(() => loadDraft(identity, { storage })).not.toThrow();
    expect(loadDraft(identity, { storage }).tables).toEqual([]);
    expect(() => addTables(identity, [table("a")], { storage })).not.toThrow();
    expect(() => addMatchingFilter(identity, { filter: { all: [] }, declaredCount: 1, pageIds: ["a"] }, { storage })).not.toThrow();
  });

  it("orders recipe operations from the canonical sequence", () => {
    for (const recipe of ["compact", "compact_and_clean_up", "metadata_only"] as const) {
      const operations = orderedRecipeOperations(recipe);
      const indexes = operations.map((operation) => MAINTENANCE_OPERATION_ORDER.indexOf(operation));
      expect(indexes.every((index) => index >= 0)).toBe(true);
      expect(indexes).toEqual([...indexes].sort((left, right) => left - right));
    }
    expect(orderedRecipeOperations("compact")).toEqual(["optimize"]);
    expect(orderedRecipeOperations("metadata_only")).toEqual(["optimize_manifests"]);
    expect(orderedRecipeOperations("compact_and_clean_up")).toEqual([
      "optimize",
      "optimize_manifests",
      "expire_snapshots",
      "remove_orphan_files"
    ]);
  });
});
