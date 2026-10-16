import { describe, expect, it } from "vitest";

import { TABLE_TAB_ALIASES, TABLE_TABS } from "./useTableDetail";

describe("table detail tabs", () => {
  it("keeps eight tabs and translates the old names", () => {
    expect([...TABLE_TABS]).toEqual([
      "overview",
      "files",
      "snapshots",
      "maintenance",
      "config",
      "usage",
      "quality",
      "cost"
    ]);
    expect(TABLE_TAB_ALIASES.structure).toBe("files");
    expect(TABLE_TAB_ALIASES["health-quality"]).toBe("quality");
    expect(TABLE_TAB_ALIASES.activity).toBe("maintenance");
    expect(TABLE_TAB_ALIASES.lineage).toBe("usage");
    expect(TABLE_TAB_ALIASES.config).toBe("config");
  });
});
