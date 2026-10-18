import { describe, expect, it } from "vitest";
import { deleteDebt, deleteSeries } from "./deleteDebt";

describe("deleteDebt", () => {
  it("uses the health check's warn and critical cut-offs on share or count", () => {
    expect(deleteDebt(2, 10)?.level).toBe("healthy");
    expect(deleteDebt(5, 10)?.level).toBe("minor");
    expect(deleteDebt(1, 150)?.level).toBe("minor");
    expect(deleteDebt(25, 10)?.level).toBe("major");
    expect(deleteDebt(1, 1_000)?.level).toBe("major");
  });

  it("says nothing when the scan recorded neither", () => {
    expect(deleteDebt(null, null)).toBeNull();
  });
});

describe("deleteSeries", () => {
  it("keeps scans in time order and leaves unrecorded values as gaps", () => {
    expect(
      deleteSeries([
        { scan_at: "2026-10-02T00:00:00Z", total_delete_files: 40, delete_file_pct: 8 },
        { scan_at: "2026-10-01T00:00:00Z" }
      ])
    ).toEqual([
      { scanAt: "2026-10-01T00:00:00Z", deleteFiles: null, deletePct: null },
      { scanAt: "2026-10-02T00:00:00Z", deleteFiles: 40, deletePct: 8 }
    ]);
  });
});
