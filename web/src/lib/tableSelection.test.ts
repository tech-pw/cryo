import { describe, expect, it } from "vitest";
import {
  computeRemovedPageIds,
  demoteFilterSelection,
  selectionModeFromState,
  visibleSelectedRowKeys
} from "./tableSelection";

describe("tableSelection", () => {
  it("derives filter mode when all matching is active", () => {
    expect(selectionModeFromState(true, [])).toBe("filter");
    expect(selectionModeFromState(false, ["a"])).toBe("ids");
    expect(selectionModeFromState(false, [])).toBe("none");
  });

  it("shows all page ids as selected in filter mode", () => {
    expect(visibleSelectedRowKeys("filter", ["a", "b"], [])).toEqual(["a", "b"]);
    expect(visibleSelectedRowKeys("ids", ["a", "b"], ["b"])).toEqual(["b"]);
  });

  it("computes removed page ids when demoting from filter mode", () => {
    expect(computeRemovedPageIds(["a", "b", "c"], ["a", "c"])).toEqual(["b"]);
  });

  it("demotes filter selection by subtracting removed ids", () => {
    expect(demoteFilterSelection(["a", "b", "c", "d"], ["b"])).toEqual(["a", "c", "d"]);
  });
});
