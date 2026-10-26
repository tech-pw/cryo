import { useParams } from "react-router-dom";
import { TableDetailShell } from "./table-detail/TableDetailShell";

/**
 * Route element for `/tables/:tableId`.
 *
 * Keyed by table id so navigating from one table to another remounts the page.
 * Staged config edits, override drafts and an open plan preview are per-table
 * state; without the key React reused the tree and those drafts were applied to
 * the next table (#192).
 */
export const TableDetail = () => {
  const { tableId = "" } = useParams();
  return <TableDetailShell key={tableId} />;
};
