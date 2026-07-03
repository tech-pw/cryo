import type { UUID } from "../api/types";

export type SelectionMode = "none" | "filter" | "ids";

export const selectionModeFromState = (allMatching: boolean, selectedRowKeys: UUID[]): SelectionMode => {
  if (allMatching) return "filter";
  if (selectedRowKeys.length > 0) return "ids";
  return "none";
};

export const visibleSelectedRowKeys = (
  mode: SelectionMode,
  pageIds: UUID[],
  selectedRowKeys: UUID[]
): UUID[] => (mode === "filter" ? pageIds : selectedRowKeys);

export const computeRemovedPageIds = (pageIds: UUID[], keys: UUID[]): UUID[] => {
  const keySet = new Set(keys);
  return pageIds.filter((id) => !keySet.has(id));
};

export const demoteFilterSelection = (allMatchingIds: UUID[], removedIds: UUID[]): UUID[] => {
  if (removedIds.length === 0) return allMatchingIds;
  const removed = new Set(removedIds);
  return allMatchingIds.filter((id) => !removed.has(id));
};
