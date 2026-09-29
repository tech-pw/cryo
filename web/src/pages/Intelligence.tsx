import { useMutation, useQuery } from "@tanstack/react-query";
import { Bot } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { getBanditStatus, getCostModelEvaluate, getQPPConfidence, resetBandit } from "../api/client";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { ConfirmAction } from "../components/confirm";
import { PageHeader } from "../components/common";
import { Alert } from "../components/ui/alert";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { useAuth } from "../lib/auth";
import { formatNumber } from "../lib/format";

const StatPair = ({ label, value }: { label: string; value: ReactNode }) => (
  <div>
    <p className="text-xs text-muted-foreground">{label}</p>
    <p className="text-lg font-semibold [font-variant-numeric:tabular-nums]">{value}</p>
  </div>
);

const num = (value: number | null | undefined, digits = 2): string => {
  if (value == null || Number.isNaN(value)) return "-";
  return value.toFixed(digits);
};

const pct = (value: number | null | undefined): string => {
  if (value == null || Number.isNaN(value)) return "-";
  return `${(value * 100).toFixed(0)}%`;
};

export const Intelligence = () => {
  const { api, role } = useAuth();
  const bandit = useQuery({
    queryKey: ["bandit-status"],
    queryFn: () => getBanditStatus(api),
    refetchInterval: 60_000
  });
  const costModel = useQuery({
    queryKey: ["cost-model-evaluate"],
    queryFn: () => getCostModelEvaluate(api),
    refetchInterval: 120_000
  });
  const qpp = useQuery({
    queryKey: ["qpp-confidence"],
    queryFn: () => getQPPConfidence(api),
    refetchInterval: 120_000
  });
  const banditReset = useMutation({
    mutationFn: () => resetBandit(api),
    onSuccess: () => {
      toast.success("Bandit state reset to heuristics");
      void bandit.refetch();
    }
  });

  return (
    <div>
      <PageHeader
        title="Intelligence"
        subtitle="Ask Cryo lives in the drawer. Model diagnostics on this page are experimental."
        actions={
          <div className="flex flex-wrap gap-2">
            <Link to="/settings/ai/models">
              <Button variant="secondary">AI models</Button>
            </Link>
            <Link to="/connections">
              <Button variant="secondary">
                <Bot className="size-4" aria-hidden="true" />
                AI providers
              </Button>
            </Link>
          </div>
        }
      />
      <Alert
        className="page-alert mb-4"
        variant="info"
        title="Ask Cryo lives in the drawer. Open it with the Ask Cryo button or ⌘I. This page no longer takes chat or tool JSON."
      />
      <details className="rounded-[var(--radius-default)] border border-border" open>
        <summary className="cursor-pointer px-4 py-3 text-sm font-medium">Experimental diagnostics</summary>
        <div className="grid gap-4 border-t border-border p-4 lg:grid-cols-3">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Governor bandit</CardTitle>
            </CardHeader>
            <CardContent>
              <AsyncBoundary
                isLoading={bandit.isLoading}
                isError={Boolean(bandit.error)}
                error={bandit.error}
                onRetry={() => void bandit.refetch()}
                isEmpty={!bandit.data}
                emptyTitle="Bandit unavailable"
                emptyDescription="Governor state file not readable."
              >
                {bandit.data ? (
                  <div className="space-y-4">
                    <div className="grid grid-cols-2 gap-4">
                      <StatPair label="Observations" value={formatNumber(bandit.data.observations)} />
                      <StatPair label="Confident" value={bandit.data.confident ? "Yes" : "No"} />
                    </div>
                    <p className="text-sm text-muted-foreground">
                      High load ratio {num(bandit.data.config.high_load_ratio, 3)} · fraction{" "}
                      {num(bandit.data.config.high_load_fraction, 3)} · cap {formatNumber(bandit.data.config.per_replica_cap)}
                    </p>
                    {role === "admin" ? (
                      <ConfirmAction
                        title="Reset admission bandit?"
                        description="Clears learned governor bandit state and reverts to static heuristics."
                        onConfirm={() => banditReset.mutate()}
                      >
                        <Button size="sm" variant="danger" loading={banditReset.isPending}>
                          Reset bandit
                        </Button>
                      </ConfirmAction>
                    ) : null}
                  </div>
                ) : null}
              </AsyncBoundary>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Learned cost model</CardTitle>
            </CardHeader>
            <CardContent>
              <AsyncBoundary
                isLoading={costModel.isLoading}
                isError={Boolean(costModel.error)}
                error={costModel.error}
                onRetry={() => void costModel.refetch()}
                isEmpty={!costModel.data}
                emptyTitle="Cost model unavailable"
                emptyDescription="No audit outcomes to evaluate yet."
              >
                {costModel.data ? (
                  <div className="space-y-3">
                    <StatPair label="MAE (learned)" value={`${num(costModel.data.mae_learned)} USD/mo`} />
                    <StatPair label="MAE (heuristic)" value={`${num(costModel.data.mae_heuristic)} USD/mo`} />
                    <StatPair label="Conformal coverage" value={pct(costModel.data.band_coverage)} />
                    <Badge variant={costModel.data.beats_heuristic ? "success" : "secondary"}>
                      {costModel.data.beats_heuristic ? "Learned model beats heuristic on holdout" : "Heuristic still competitive"}
                    </Badge>
                  </div>
                ) : null}
              </AsyncBoundary>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Query performance prediction (QPP)</CardTitle>
            </CardHeader>
            <CardContent>
              <AsyncBoundary
                isLoading={qpp.isLoading}
                isError={Boolean(qpp.error)}
                error={qpp.error}
                onRetry={() => void qpp.refetch()}
                isEmpty={!qpp.data}
                emptyTitle="QPP unavailable"
                emptyDescription="No query performance predictions recorded yet."
              >
                {qpp.data ? (
                  <div className="space-y-3">
                    <Badge variant={qpp.data.qpp_enabled ? "success" : "secondary"}>
                      {qpp.data.qpp_enabled ? "Admission gating enabled" : "Advisory only"}
                    </Badge>
                    <StatPair label="Observations" value={formatNumber(qpp.data.observations)} />
                    <StatPair label="Confidence" value={pct(qpp.data.confidence)} />
                  </div>
                ) : null}
              </AsyncBoundary>
            </CardContent>
          </Card>
        </div>
      </details>
    </div>
  );
};
