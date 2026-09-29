import type { ColumnDef } from "@tanstack/react-table";
import { useMemo } from "react";
import { formatAssistantCell, type AssistantBlock, type AssistantColumn } from "../../../api/assistant";
import { DataTable } from "../../ui/data-table";
import { EvidenceLinks } from "./EvidenceLinks";

const columnHeader = (column: AssistantColumn): string => column.header ?? column.label ?? column.key;

export const assistantTableColumns = (
  columns: AssistantColumn[]
): ColumnDef<Record<string, unknown>, unknown>[] =>
  columns.map((column) => ({
    id: column.key,
    header: columnHeader(column),
    cell: ({ row }) => (
      <span className={column.align === "right" ? "block text-right [font-variant-numeric:tabular-nums]" : undefined}>
        {formatAssistantCell(row.original[column.key])}
      </span>
    )
  }));

export const AssistantBlockTable = ({
  title,
  columns,
  rows
}: {
  title?: string;
  columns?: AssistantColumn[];
  rows?: Array<Record<string, unknown>>;
}) => {
  const safeRows = rows ?? [];
  const safeColumns =
    columns && columns.length > 0
      ? columns
      : Object.keys(safeRows[0] ?? {})
          .filter((key) => key !== "id")
          .map((key) => ({ key, header: key }));
  const tableColumns = useMemo(() => assistantTableColumns(safeColumns), [safeColumns]);
  return (
    <div className="space-y-2">
      {title ? <h3 className="text-sm font-medium text-foreground">{title}</h3> : null}
      <DataTable
        columns={tableColumns}
        data={safeRows}
        density="compact"
        getRowId={(row, index) => String(row.id ?? `row-${index}`)}
        emptyTitle="No rows"
        emptyDescription="This block did not include rows."
      />
    </div>
  );
};

export const RankedTableBlock = ({ block }: { block: Extract<AssistantBlock, { type: "ranked_table" }> }) => (
  <div>
    <AssistantBlockTable title={block.title} columns={block.columns} rows={block.rows} />
    <EvidenceLinks ids={block.evidence_ids} />
  </div>
);
