import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchAllMatchingTableIds } from "../api/client";
import type { TableRead } from "../api/types";
import { useTableExplorerSelection } from "./useTableExplorerSelection";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    fetchAllMatchingTableIds: vi.fn()
  };
});

const api = { token: "operator" as const };

const makePageItems = (): TableRead[] => [
  {
    id: "t1",
    source_id: "s1",
    schema_name: "demo",
    table_name: "a",
    first_seen_at: "",
    is_active: true
  },
  {
    id: "t2",
    source_id: "s1",
    schema_name: "demo",
    table_name: "b",
    first_seen_at: "",
    is_active: true
  }
];

describe("useTableExplorerSelection", () => {
  beforeEach(() => {
    vi.mocked(fetchAllMatchingTableIds).mockReset();
  });

  it("shows all page ids as selected in filter mode", () => {
    const { result } = renderHook(() =>
      useTableExplorerSelection({
        api,
        predicate: null,
        matchingTotal: 2,
        pageItems: makePageItems()
      })
    );

    act(() => {
      result.current.selectAllMatching();
    });

    expect(result.current.filterSelectionActive).toBe(true);
    expect(result.current.rowSelectedKeys).toEqual(["t1", "t2"]);
  });

  it("demotes to ids when unchecking a row in filter mode", async () => {
    vi.mocked(fetchAllMatchingTableIds).mockResolvedValue(["t1", "t2", "t3"]);

    const { result } = renderHook(() =>
      useTableExplorerSelection({
        api,
        predicate: null,
        matchingTotal: 3,
        pageItems: makePageItems()
      })
    );

    act(() => {
      result.current.selectAllMatching();
    });

    await act(async () => {
      await result.current.handleRowSelectionChange(["t1"]);
    });

    await waitFor(() => {
      expect(result.current.filterSelectionActive).toBe(false);
    });
    expect(result.current.selectedRowKeys).toEqual(["t1", "t3"]);
    expect(result.current.hasSelection).toBe(true);
  });

  it("clears selection on reset", () => {
    const { result } = renderHook(() =>
      useTableExplorerSelection({
        api,
        predicate: null,
        matchingTotal: 2,
        pageItems: makePageItems()
      })
    );

    act(() => {
      result.current.selectAllMatching();
      result.current.resetSelectionForFilterChange();
    });

    expect(result.current.hasSelection).toBe(false);
    expect(result.current.rowSelectedKeys).toEqual([]);
  });
});
