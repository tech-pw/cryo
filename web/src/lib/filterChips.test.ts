import { describe, expect, it, vi } from "vitest";
import { buildFilterChips, filterScopeLabel } from "./filterChips";

describe("buildFilterChips", () => {
  it("builds removable chips for active filters", () => {
    const onPatch = vi.fn();
    const chips = buildFilterChips(
      {
        activeOnly: true,
        grades: ["B"],
        priorities: ["MEDIUM"],
        needsCompaction: true,
        sourceIds: ["catalog-1"],
        presetFilter: ["__none__", "preset-1"]
      },
      [{ label: "Prod", value: "catalog-1" }],
      onPatch,
      [{ label: "Nightly", value: "preset-1" }]
    );

    expect(chips.map((chip) => chip.label)).toEqual([
      "Catalog: Prod",
      "Preset: No preset, Nightly",
      "Grade: B",
      "Priority: Medium",
      "Needs compaction"
    ]);

    chips[0]?.onRemove();
    expect(onPatch).toHaveBeenCalledWith({ sourceIds: undefined });
  });
});

describe("filterScopeLabel", () => {
  it("uses standardized default labels", () => {
    expect(filterScopeLabel({ activeOnly: true }, [])).toBe("Active tables");
    expect(filterScopeLabel({ activeOnly: false }, [])).toBe("All tables");
    expect(filterScopeLabel({ activeOnly: true }, [{ key: "grade", label: "Grade: B", onRemove: vi.fn() }])).toBe(
      "Filtered view"
    );
  });
});
