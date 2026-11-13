import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import type { TablesFunnel } from "../../api/types";
import { FlowSankey, type FlowNode } from "../../components/charts/FlowSankey";
import { Skeleton } from "../../components/ui/skeleton";
import { formatNumber } from "../../lib/format";
import { nodeColor } from "../catalog/MaintenanceFunnelSankey";
import { formatAgo } from "./homeSignals";
import { HomeCard } from "./HomeCard";

const count = (funnel: TablesFunnel, id: string): number | null => funnel.nodes.find((node) => node.id === id)?.count ?? null;

/** One sentence with the night's main finding, built only from nodes the funnel reports. */
export const flowHeadline = (funnel: TablesFunnel): string => {
  const active = funnel.total_active;
  const succeeded = count(funnel, "succeeded");
  const failed = count(funnel, "failed");
  const noWork = count(funnel, "no_work");
  const parts: string[] = [];
  if (succeeded != null) parts.push(`${formatNumber(succeeded)} of ${formatNumber(active)} active tables were maintained`);
  else parts.push(`${formatNumber(active)} active tables were considered`);
  if (noWork) parts.push(`${formatNumber(noWork)} needed nothing`);
  if (failed) parts.push(`${formatNumber(failed)} failed`);
  return `${parts.join(", ")}.`;
};

export const FlowCard = ({ funnel, loading, error }: { funnel: TablesFunnel | null | undefined; loading: boolean; error: boolean }) => {
  const navigate = useNavigate();
  const views = useMemo(() => new Map((funnel?.nodes ?? []).map((node) => [node.id, node.view ?? null])), [funnel]);
  const nodes = useMemo<FlowNode[]>(
    () => (funnel?.nodes ?? []).map((node) => ({ id: node.id, label: node.label, count: node.count, color: nodeColor(node.id), clickable: Boolean(node.view) })),
    [funnel]
  );
  const when = funnel?.run_finished_at ?? funnel?.run_started_at ?? null;
  return (
    <HomeCard
      id="home-flow"
      title="Last night, table by table"
      description={`Where every active table landed in the latest nightly run${when ? `, ${formatAgo(when)}` : ""}. Hover a stage to trace it; select one to list its tables.`}
      action={funnel?.run_id ? { label: "Open the run", href: `/runs/${funnel.run_id}` } : undefined}
      busy={loading}
      className="home-flow"
    >
      {loading ? <Skeleton className="h-[280px] w-full" /> : error ? <p className="home-empty">The nightly flow is unavailable right now.</p> : !funnel?.run_id || !funnel.links.length ? (
        <p className="home-empty">The flow appears after the first scheduled nightly run.</p>
      ) : (
        <FlowSankey
          testId="home-flow"
          nodes={nodes}
          links={funnel.links}
          unit="tables"
          height={280}
          summary={flowHeadline(funnel)}
          tableCaption="Where active tables landed in the latest nightly run"
          formatCount={(value) => formatNumber(value)}
          onNodeClick={(id) => {
            const view = views.get(id);
            if (view) navigate(`/tables?view=${encodeURIComponent(view)}`);
          }}
        />
      )}
    </HomeCard>
  );
};
