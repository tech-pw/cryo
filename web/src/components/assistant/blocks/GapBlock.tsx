import { Link } from "react-router-dom";
import type { AssistantBlock } from "../../../api/assistant";
import { Alert } from "../../ui/alert";
import { EvidenceLinks } from "./EvidenceLinks";

export const GapBlock = ({ block }: { block: Extract<AssistantBlock, { type: "gap" }> }) => (
  <div className="space-y-2">
    <Alert
      variant="warning"
      title="Gap"
      description={block.text || block.missing_input || block.reason || "This answer is missing evidence."}
    />
    {block.setup_url?.startsWith("/") ? (
      <Link to={block.setup_url} className="text-sm text-primary underline-offset-2 hover:underline">
        Open setup
      </Link>
    ) : null}
    <EvidenceLinks ids={block.evidence_ids} />
  </div>
);
