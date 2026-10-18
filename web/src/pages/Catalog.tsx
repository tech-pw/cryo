import { Link } from "react-router-dom";
import { PageHeader } from "../components/common";
import { formatRelative } from "./operations/runState";
import { CatalogDashboard } from "./catalog/CatalogDashboard";
import { TableExplorerSection } from "./catalog/TableExplorerSection";
import { useTablesFunnel } from "./catalog/tablesTriage";

const RUN_TONE: Record<string, string> = {
  succeeded: "bg-[var(--color-status-success)]",
  failed: "bg-[var(--color-status-danger)]",
  partial: "bg-[var(--color-status-warning)]",
  running: "bg-[var(--color-primary)] motion-safe:animate-pulse"
};

const NightlyRunStatus = () => {
  const funnel = useTablesFunnel();
  const run = funnel.data;
  if (!run?.run_id) return null;
  const status = run.run_status ?? "unknown";
  return (
    <Link
      to={`/runs/${run.run_id}`}
      className="inline-flex items-center gap-2 rounded-full border border-border px-3 py-1 text-xs text-muted-foreground hover:border-[var(--color-border-strong)] hover:text-foreground"
    >
      <span className={`size-2 rounded-full ${RUN_TONE[status] ?? "bg-muted-foreground/50"}`} aria-hidden="true" />
      <span>
        Last nightly run <span className="font-medium capitalize text-foreground">{status.replaceAll("_", " ")}</span>
        {" · "}
        {formatRelative(run.run_finished_at ?? run.run_started_at)}
      </span>
    </Link>
  );
};

/**
 * Tables: every Iceberg table Cryo watches. Triage cards and the nightly funnel at the top set the
 * explorer's `view`; catalog rows set its `catalog` filter. Everything round-trips through the URL.
 */
export const Catalog = () => (
  <div>
    <PageHeader
      title="Tables"
      subtitle="Health, maintenance status and savings for every Iceberg table. Start with what failed or was skipped."
      actions={<NightlyRunStatus />}
      breadcrumbs={false}
    />
    <CatalogDashboard />
    <TableExplorerSection />
  </div>
);
