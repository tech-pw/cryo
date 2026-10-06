import type { AssistantBlock } from "../../../api/assistant";
import { EvidenceLinks } from "./EvidenceLinks";
import { VerifiedBadge } from "./VerifiedBadge";

export const AnswerBlock = ({ block }: { block: Extract<AssistantBlock, { type: "answer" }> }) => (
  <div className="space-y-2">
    {block.verified ? <VerifiedBadge handler={block.verified_handler} /> : null}
    <p className="m-0 whitespace-pre-wrap text-sm text-foreground">{block.text}</p>
    <EvidenceLinks ids={block.evidence_ids} />
  </div>
);
