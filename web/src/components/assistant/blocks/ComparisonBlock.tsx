import type { AssistantBlock, AssistantColumn } from "../../../api/assistant";
import { formatAssistantCell } from "../../../api/assistant";
import { AssistantBlockTable } from "./RankedTableBlock";
import { EvidenceLinks } from "./EvidenceLinks";

const sideRows = (block: Extract<AssistantBlock, { type: "comparison" }>): Array<Record<string, unknown>> => {
  if (block.rows?.length) return block.rows;
  if (!block.left && !block.right) return [];
  return [
    { id: "left", side: "Left", ...(block.left ?? {}) },
    { id: "right", side: "Right", ...(block.right ?? {}) }
  ];
};

const sideColumns = (block: Extract<AssistantBlock, { type: "comparison" }>, rows: Array<Record<string, unknown>>): AssistantColumn[] => {
  if (block.columns?.length) return block.columns;
  const keys = new Set<string>();
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (key !== "id") keys.add(key);
    }
  }
  return [...keys].map((key) => ({ key, header: key === "side" ? "Side" : key }));
};

export const ComparisonBlock = ({ block }: { block: Extract<AssistantBlock, { type: "comparison" }> }) => {
  const rows = sideRows(block);
  return (
    <div className="space-y-2">
      <AssistantBlockTable title={block.title ?? "Comparison"} columns={sideColumns(block, rows)} rows={rows} />
      {block.note ? <p className="text-xs text-muted-foreground">{block.note}</p> : null}
      {block.unit ? <p className="text-xs text-muted-foreground">{formatAssistantCell(block.unit)}</p> : null}
      <EvidenceLinks ids={block.evidence_ids} />
    </div>
  );
};
