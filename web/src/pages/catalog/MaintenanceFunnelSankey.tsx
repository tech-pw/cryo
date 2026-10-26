import { useMemo } from "react";
import { Link } from "react-router-dom";
import type { TablesFunnel } from "../../api/types";
import { FlowSankey, type FlowNode } from "../../components/charts/FlowSankey";
import { formatNumber } from "../../lib/format";
import { formatRelative } from "../operations/runState";

const NODE_TONE: Record<string, string> = {
  scan_failing: "var(--color-status-danger)",
  failed: "var(--color-status-danger)",
  no_work: "var(--color-status-success)",
  succeeded: "var(--color-status-success)",
  not_scanned: "var(--color-text-tertiary)",
  not_evaluated: "var(--color-text-tertiary)",
  not_in_run: "var(--color-text-tertiary)",
  not_run: "var(--color-text-tertiary)",
  in_progress: "var(--color-primary)"
};

const nodeColor = (id: string): string =>
  NODE_TONE[id] ?? (id.startsWith("skip_") ? "var(--color-status-warning)" : "var(--color-primary)");

export const MaintenanceFunnelSankey = ({
  funnel,
  activeView,
  onSelectView
}: {
  funnel: TablesFunnel;
  activeView: string | null;
  onSelectView: (view: string) => void;
}) => {
  const views = useMemo(() => new Map(funnel.nodes.map((node) => [node.id, node.view ?? null])), [funnel.nodes]);
  const nodes = useMemo<FlowNode[]>(
    () =>
      funnel.nodes.map((node) => ({
        id: node.id,
        label: node.label,
        count: node.count,
        color: nodeColor(node.id),
        clickable: Boolean(node.view),
        selected: Boolean(node.view) && node.view === activeView
      })),
    [funnel.nodes, activeView]
  );
  return (
    <FlowSankey
      testId="maintenance-funnel"
      nodes={nodes}
      links={funnel.links}
      unit="tables"
      formatCount={(value) => formatNumber(value)}
      onNodeClick={(id) => {
        const view = views.get(id);
        if (view) onSelectView(view);
      }}
    />
  );
};

export const FunnelCaption = ({ funnel }: { funnel: TablesFunnel }) =>
  funnel.run_id ? (
    <p className="text-xs text-muted-foreground">
      Where each active table landed in the{" "}
      <Link className="underline underline-offset-2 hover:text-foreground" to={`/runs/${funnel.run_id}`}>
        latest nightly run
      </Link>{" "}
      ({formatRelative(funnel.run_started_at)}). A job is that table's outcome. A table with no job and no
      recorded decision was not in the run. Counted per table, not per operation. Click a stage to list those
      tables as they are now.
    </p>
  ) : null;
