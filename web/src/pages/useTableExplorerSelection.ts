import { toast } from "sonner";
import type { Key } from "react";
import { useCallback, useMemo, useRef, useState } from "react";
import { fetchAllMatchingTableIds, type ApiOptions } from "../api/client";
import type { TableRead, UUID } from "../api/types";
import type { BulkSelection } from "../components/BulkAssignBar";
import {
  computeRemovedPageIds,
  demoteFilterSelection,
  selectionModeFromState,
  visibleSelectedRowKeys,
  type SelectionMode
} from "../lib/tableSelection";
import type { Predicate } from "../lib/predicate";

interface UseTableExplorerSelectionArgs {
  api: ApiOptions;
  predicate: Predicate | null;
  sort?: string;
  matchingTotal: number;
  pageItems: TableRead[];
}

export const useTableExplorerSelection = ({
  api,
  predicate,
  sort,
  matchingTotal,
  pageItems
}: UseTableExplorerSelectionArgs) => {
  const [selectedRowKeys, setSelectedRowKeys] = useState<UUID[]>([]);
  const [allMatching, setAllMatching] = useState(false);
  const [isDemotingSelection, setIsDemotingSelection] = useState(false);
  const selectionEpochRef = useRef(0);

  const pageIds = useMemo(() => pageItems.map((table) => table.id), [pageItems]);
  const mode: SelectionMode = selectionModeFromState(allMatching, selectedRowKeys);
  const hasSelection = mode !== "none";
  const filterSelectionActive = mode === "filter";

  const bulkSelection: BulkSelection | null = useMemo(() => {
    if (mode === "filter") {
      return { mode: "filter", filter: predicate, totalSelected: matchingTotal };
    }
    if (mode === "ids") {
      return { mode: "ids", ids: selectedRowKeys, totalSelected: selectedRowKeys.length };
    }
    return null;
  }, [mode, predicate, matchingTotal, selectedRowKeys]);

  const rowSelectedKeys = useMemo(
    () => visibleSelectedRowKeys(mode, pageIds, selectedRowKeys),
    [mode, pageIds, selectedRowKeys]
  );

  const clearSelection = useCallback(() => {
    selectionEpochRef.current += 1;
    setSelectedRowKeys([]);
    setAllMatching(false);
    setIsDemotingSelection(false);
  }, []);

  const selectAllMatching = useCallback(() => {
    selectionEpochRef.current += 1;
    setSelectedRowKeys([]);
    setAllMatching(true);
  }, []);

  const resetSelectionForFilterChange = useCallback(() => {
    clearSelection();
  }, [clearSelection]);

  const handleRowSelectionChange = useCallback(
    async (keys: Key[]) => {
      const nextKeys = keys as UUID[];

      if (allMatching) {
        const removedIds = computeRemovedPageIds(pageIds, nextKeys);
        if (removedIds.length === 0) return;

        const epoch = selectionEpochRef.current;
        setIsDemotingSelection(true);
        const controller = new AbortController();

        try {
          const allIds = await fetchAllMatchingTableIds(api, {
            predicate,
            sort,
            total: matchingTotal,
            signal: controller.signal
          });
          if (epoch !== selectionEpochRef.current) return;

          const demoted = demoteFilterSelection(allIds, removedIds);
          setAllMatching(false);
          setSelectedRowKeys(demoted);
        } catch (error) {
          if (controller.signal.aborted || epoch !== selectionEpochRef.current) return;
          toast.error(
            error instanceof Error ? error.message : "Could not resolve matching tables for selection"
          );
        } finally {
          if (epoch === selectionEpochRef.current) {
            setIsDemotingSelection(false);
          }
        }
        return;
      }

      setSelectedRowKeys(nextKeys);
    },
    [allMatching, api, matchingTotal, pageIds, predicate, sort]
  );

  return {
    selectedRowKeys,
    allMatching,
    mode,
    hasSelection,
    filterSelectionActive,
    bulkSelection,
    rowSelectedKeys,
    isDemotingSelection,
    clearSelection,
    selectAllMatching,
    resetSelectionForFilterChange,
    handleRowSelectionChange
  };
};
