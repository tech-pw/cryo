import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "../../api/client";
import { useAuth } from "../../lib/auth";
import { cn } from "../../lib/cn";
import { funnelCountLabel } from "../../lib/funnelLabel";
import "./decisionFunnel.css";

interface FunnelStage {
  stage: string;
  count: number | null;
  reason: string | null;
}

const stageLabel = (stage: string): string => stage.replaceAll("_", " ");

const stageWeight = (count: number | null, peak: number): number => {
  if (count == null || peak <= 0) return 0.35;
  return Math.max(0.28, count / peak);
};

export const DecisionFunnelStrip = () => {
  const { api, token } = useAuth();
  const funnel = useQuery({
    queryKey: ["decision-funnel", token],
    queryFn: () => apiFetch<{ run_id: string | null; stages: FunnelStage[] }>("/decisions/funnel", api),
    retry: false
  });
  const stages = funnel.data?.stages ?? [];
  const peak = stages.reduce((max, stage) => Math.max(max, stage.count ?? 0), 0);

  return (
    <section className="ops-signal-card" aria-labelledby="nightly-funnel-title">
      <header className="ops-signal-card__header">
        <h2 id="nightly-funnel-title">Last nightly funnel</h2>
        <p>How the last batch narrowed. A missing stage stays Not recorded.</p>
      </header>
      {funnel.isError ? (
        <p className="ops-signal-card__empty">Funnel unavailable.</p>
      ) : stages.length === 0 ? (
        <p className="ops-signal-card__empty">No funnel recorded yet.</p>
      ) : (
        <ol className="ops-funnel">
          {stages.map((stage, index) => {
            const failed = stage.stage === "failed";
            const previous = index > 0 ? stages[index - 1].count : null;
            const dropped =
              !failed && previous != null && stage.count != null && previous > stage.count
                ? previous - stage.count
                : null;
            return (
              <li
                key={stage.stage}
                className={cn("ops-funnel__stage", failed && "is-failed", stage.count == null && "is-missing")}
                style={{ flexGrow: stageWeight(stage.count, peak), flexBasis: 0 }}
              >
                <span className="ops-funnel__count">{funnelCountLabel(stage.count)}</span>
                <span className="ops-funnel__name">{stageLabel(stage.stage)}</span>
                {dropped != null ? <span className="ops-funnel__drop">−{dropped}</span> : null}
                {stage.reason ? <span className="ops-funnel__reason">{stage.reason}</span> : null}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
};
