import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  authorizeAssistantProposal,
  formatAssistantCell,
  getAssistantCapabilities,
  getAssistantProposal,
  ProposalConflictError,
  rejectAssistantProposal,
  type AssistantBlock
} from "../../../api/assistant";
import { useAuth } from "../../../lib/auth";
import { formatTime } from "../../../lib/format";
import { roleAllows } from "../../../lib/permissions";
import { useWriteControl } from "../../../lib/useWriteControl";
import { Alert } from "../../ui/alert";
import { Button } from "../../ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../ui/card";

const meetsRole = (role: string, required?: string): boolean => {
  if (!required || required === "viewer") return true;
  if (required === "admin") return role === "admin";
  return roleAllows(role as "viewer" | "operator" | "admin", "operator");
};

export const ActionReviewCard = ({
  proposalId,
  block
}: {
  proposalId: string;
  block?: Extract<AssistantBlock, { type: "action_review" }>;
}) => {
  const { api, token, role } = useAuth();
  const queryClient = useQueryClient();
  const write = useWriteControl("runMaintenance");
  const idempotencyKey = useRef<string | null>(null);
  const [conflictId, setConflictId] = useState<string | null | undefined>(undefined);
  const proposal = useQuery({
    queryKey: ["assistant", "proposal", proposalId, token],
    queryFn: () => getAssistantProposal(api, proposalId),
    retry: false
  });
  const capabilities = useQuery({
    queryKey: ["assistant", "capabilities", token],
    queryFn: () => getAssistantCapabilities(api),
    staleTime: 60_000,
    retry: false
  });
  const current = proposal.data;
  const preview = current?.preview ?? block?.preview ?? null;
  const requiredRole = current?.required_role ?? block?.required_role;
  const writesOff = capabilities.data?.writes_enabled === false;
  const allowed = Boolean(current) && write.allowed && meetsRole(role, requiredRole) && !writesOff;
  const reason = !current
    ? null
    : writesOff
      ? "Writes are disabled for Ask Cryo."
      : !meetsRole(role, requiredRole)
        ? `Requires ${requiredRole}.`
        : write.reason;

  const authorize = useMutation({
    mutationFn: () => {
      const key = idempotencyKey.current ?? crypto.randomUUID();
      idempotencyKey.current = key;
      return authorizeAssistantProposal(api, proposalId, { idempotency_key: key, channel: "ui_card" });
    },
    onSuccess: (data) => {
      idempotencyKey.current = null;
      setConflictId(undefined);
      queryClient.setQueryData(["assistant", "proposal", proposalId, token], data);
    },
    onError: (error) => {
      if (error instanceof ProposalConflictError) setConflictId(error.revisedProposalId);
    }
  });
  const reject = useMutation({
    mutationFn: () => rejectAssistantProposal(api, proposalId),
    onSuccess: (data) => {
      queryClient.setQueryData(["assistant", "proposal", proposalId, token], data);
    }
  });

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{block?.title ?? current?.action_type ?? "Review action"}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <dl className="space-y-1">
          <div className="flex justify-between gap-3">
            <dt className="text-muted-foreground">Status</dt>
            <dd>{formatAssistantCell(current?.status ?? block?.status)}</dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="text-muted-foreground">Tier</dt>
            <dd>{formatAssistantCell(current?.tier ?? block?.tier)}</dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="text-muted-foreground">Expires</dt>
            <dd>{formatTime(current?.expires_at ?? block?.expires_at)}</dd>
          </div>
        </dl>
        {preview ? (
          <dl className="space-y-1 border-t border-border pt-2">
            {Object.entries(preview).map(([key, value]) => (
              <div key={key} className="flex justify-between gap-3">
                <dt className="text-muted-foreground">{key}</dt>
                <dd className="text-right">{formatAssistantCell(value)}</dd>
              </div>
            ))}
          </dl>
        ) : null}
        {current?.last_error ? <Alert variant="error" title="Last error" description={current.last_error} /> : null}
        {conflictId !== undefined ? (
          <Alert
            variant="warning"
            title="The plan changed"
            description={
              conflictId ? (
                <Link to={`/assistant/approve/${conflictId}`} className="underline">
                  Revised proposal {conflictId}
                </Link>
              ) : (
                "Review the updated plan before authorizing."
              )
            }
          />
        ) : null}
        {authorize.isError && !(authorize.error instanceof ProposalConflictError) ? (
          <Alert variant="error" title="Authorize failed" description={(authorize.error as Error).message} />
        ) : null}
        {reject.isError ? <Alert variant="error" title="Reject failed" description={(reject.error as Error).message} /> : null}
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="primary"
            disabled={!allowed || authorize.isPending}
            loading={authorize.isPending}
            title={reason ?? undefined}
            onClick={() => authorize.mutate()}
          >
            Authorize
          </Button>
          <Button
            type="button"
            variant="secondary"
            disabled={!current || reject.isPending}
            loading={reject.isPending}
            onClick={() => reject.mutate()}
          >
            Reject
          </Button>
        </div>
        {reason && !allowed ? <p className="text-xs text-muted-foreground">{reason}</p> : null}
      </CardContent>
    </Card>
  );
};
