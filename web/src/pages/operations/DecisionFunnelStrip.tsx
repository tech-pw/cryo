import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "../../api/client";
import { useAuth } from "../../lib/auth";
import { funnelCountLabel } from "../../lib/funnelLabel";

interface FunnelStage {
  stage: string;
  count: number | null;
  reason: string | null;
}

export const DecisionFunnelStrip = () => {
  const { api, token } = useAuth();
  const funnel = useQuery({
    queryKey: ["decision-funnel", token],
    queryFn: () => apiFetch<{ run_id: string | null; stages: FunnelStage[] }>("/decisions/funnel", api),
    retry: false
  });
  const stages = funnel.data?.stages ?? [];

  return (
    <section className="mb-4 rounded-md border border-[var(--color-border-default)] px-3 py-2">
      <h2 className="text-sm font-medium">Last nightly funnel</h2>
      <p className="text-xs text-muted-foreground">
        Stages we cannot defend from the stored batch summary stay Not recorded.
      </p>
      {funnel.isError ? (
        <p className="mt-2 text-sm">Funnel unavailable.</p>
      ) : (
        <ol className="mt-2 flex flex-wrap gap-2">
          {stages.map((stage) => (
            <li key={stage.stage} className="rounded-full border px-2 py-1 text-xs">
              {stage.stage}: {funnelCountLabel(stage.count)}
              {stage.reason ? ` (${stage.reason})` : ""}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
};
