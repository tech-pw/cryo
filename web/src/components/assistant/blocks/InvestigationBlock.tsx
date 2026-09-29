import type { AssistantBlock } from "../../../api/assistant";
import { formatAssistantCell } from "../../../api/assistant";
import { EvidenceLinks } from "./EvidenceLinks";

export const InvestigationBlock = ({ block }: { block: Extract<AssistantBlock, { type: "investigation" }> }) => {
  const findings =
    block.findings ??
    block.hypotheses?.map((item) => ({
      label: typeof item.label === "string" ? item.label : typeof item.name === "string" ? item.name : undefined,
      detail: typeof item.detail === "string" ? item.detail : typeof item.reason === "string" ? item.reason : undefined
    }));
  return (
    <section className="space-y-2">
      {block.title ? <h3 className="text-sm font-medium">{block.title}</h3> : null}
      {block.summary || block.symptom ? <p className="m-0 whitespace-pre-wrap text-sm">{block.summary ?? block.symptom}</p> : null}
      {findings?.length ? (
        <ul className="space-y-1 text-sm">
          {findings.map((finding, index) => (
            <li key={`${finding.label ?? "finding"}-${index}`}>
              <span className="font-medium">{formatAssistantCell(finding.label)}</span>
              {finding.detail ? <span className="text-muted-foreground"> · {formatAssistantCell(finding.detail)}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {block.missing_inputs?.length ? (
        <p className="text-xs text-muted-foreground">Missing {block.missing_inputs.join(", ")}</p>
      ) : null}
      {block.next_step ? <p className="text-sm">{block.next_step}</p> : null}
      <EvidenceLinks ids={block.evidence_ids} />
    </section>
  );
};
