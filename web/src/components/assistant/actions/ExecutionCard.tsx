import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { formatAssistantCell, getAssistantProposal, isTerminalProposalStatus } from "../../../api/assistant";
import { useAuth } from "../../../lib/auth";
import { Card, CardContent, CardHeader, CardTitle } from "../../ui/card";

export const ExecutionCard = ({ proposalId }: { proposalId: string }) => {
  const { api, token } = useAuth();
  const proposal = useQuery({
    queryKey: ["assistant", "proposal", proposalId, token],
    queryFn: () => getAssistantProposal(api, proposalId),
    refetchInterval: (query) => {
      if (typeof document !== "undefined" && document.hidden) return false;
      if (isTerminalProposalStatus(query.state.data?.status)) return false;
      return 5_000;
    },
    refetchIntervalInBackground: false,
    retry: false
  });
  const status = proposal.data?.status;
  const runId = proposal.data?.run_id;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">Execution</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <p>
          Status <span className="font-medium">{formatAssistantCell(status)}</span>
        </p>
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
