import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { formatAssistantCell, getAssistantProposal, type AssistantBlock } from "../../../api/assistant";
import { useAuth } from "../../../lib/auth";
import { Card, CardContent, CardHeader, CardTitle } from "../../ui/card";

export const OutcomeCard = ({ block }: { block: Extract<AssistantBlock, { type: "outcome" }> }) => {
  const { api, token } = useAuth();
  const proposal = useQuery({
    queryKey: ["assistant", "proposal", block.proposal_id, token],
    queryFn: () => getAssistantProposal(api, block.proposal_id ?? ""),
    enabled: Boolean(block.proposal_id),
    retry: false
  });
  const status = proposal.data?.status ?? block.status;
  const runId = proposal.data?.run_id ?? block.run_id;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">Outcome</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <p>
          Status <span className="font-medium">{formatAssistantCell(status)}</span>
        </p>
        <dl className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
          <div>Requested {formatAssistantCell(block.requested)}</div>
          <div>Completed {formatAssistantCell(block.completed)}</div>
          <div>Failed {formatAssistantCell(block.failed)}</div>
          <div>Skipped {formatAssistantCell(block.skipped)}</div>
        </dl>
        {block.summary ? <p className="whitespace-pre-wrap">{block.summary}</p> : null}
        {proposal.data?.last_error ? <p className="text-muted-foreground">{proposal.data.last_error}</p> : null}
        {runId ? (
          <Link to={`/runs/${runId}`} className="text-primary underline-offset-2 hover:underline">
            Open run
          </Link>
        ) : null}
      </CardContent>
    </Card>
  );
};
