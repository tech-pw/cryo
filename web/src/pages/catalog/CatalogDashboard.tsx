import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { getMultiCatalogDashboard, type MultiCatalogDashboard } from "../../api/client";
import { AsyncBoundary } from "../../components/AsyncBoundary";
import { ChartCard } from "../../components/table-detail/ChartCard";
import { healthScoreColor } from "../../components/status";
import { Alert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Skeleton } from "../../components/ui/skeleton";
import { MetricTile } from "../../components/ui/stat";
import { useAuth } from "../../lib/auth";
import { formatCurrency, formatGb, formatNumber, formatSavings } from "../../lib/format";
import { DEFAULT_TABLE_SORT, SCAN_STALE_HOURS } from "../../lib/tableFilters";
import { FunnelCaption, MaintenanceFunnelSankey } from "./MaintenanceFunnelSankey";
import {
  TRIAGE_CARD_IDS,
  TRIAGE_VIEWS,
  useStaleScanCount,
  useTablesFunnel,
  useTriageCounts
} from "./tablesTriage";

type CatalogStats = MultiCatalogDashboard["catalogs"][number];

const CATALOG_FLEET_COST_TOOLTIP =
  "Modeled sum of scanner-estimated monthly S3 cost for tables with stats on enabled catalogs. Not a bill.";

/** Share of active tables that must be stale before the page calls the scanner behind. */
const SCANNER_LAG_SHARE = 0.1;

const scrollToExplorer = () =>
  document.getElementById("table-explorer")?.scrollIntoView({ block: "start", behavior: "smooth" });

/** Quick views and catalog clicks drive the explorer below through the URL, never local state. */
export const useExplorerViewParam = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const view = searchParams.get("view");
  const setView = (next: string | null, options: { toggle?: boolean; scroll?: boolean } = {}) => {
    const params = new URLSearchParams(searchParams);
    const target = options.toggle && view === next ? null : next;
    if (target) params.set("view", target);
    else params.delete("view");
    if (target === "savings") params.set("sort", "-projected_savings_usd_month");
    else if (view === "savings" && params.get("sort") === "-projected_savings_usd_month") {
      params.set("sort", DEFAULT_TABLE_SORT);
    }
    params.delete("page");
    setSearchParams(params, { replace: true });
    if (options.scroll) scrollToExplorer();
  };
  return { view, setView, searchParams, setSearchParams };
};

const TriageCards = () => {
  const { counts, loading } = useTriageCounts();
  const { view, setView } = useExplorerViewParam();
  return (
    <section aria-label="Maintenance triage" className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
      {TRIAGE_CARD_IDS.map((id) => {
        const meta = TRIAGE_VIEWS[id];
        const count = counts[id];
        return (
          <MetricTile
            key={id}
            label={meta.label}
            value={count == null ? "—" : formatNumber(count)}
            loading={loading && count == null}
            emphasis={(meta.tone === "danger" || meta.tone === "warning") && (count ?? 0) > 0}
            delta={{ value: meta.hint, tone: "neutral" }}
            pressed={view === id}
            onClick={() => setView(id, { toggle: true, scroll: true })}
          />
        );
      })}
    </section>
  );
};

const FunnelCard = () => {
  const funnel = useTablesFunnel();
  const { view, setView } = useExplorerViewParam();
  return (
    <ChartCard title="Last nightly run" className="xl:col-span-2">
      {funnel.isLoading ? (
        <Skeleton className="h-[260px] w-full" />
      ) : funnel.isError ? (
        <Alert variant="error" title="Could not load the nightly funnel" />
      ) : !funnel.data?.run_id ? (
        <p className="py-10 text-center text-sm text-muted-foreground">
          No nightly batch has run yet. The funnel appears after the first scheduled run.
        </p>
      ) : (
        <>
          <MaintenanceFunnelSankey
            funnel={funnel.data}
            activeView={view}
            onSelectView={(next) => setView(next, { toggle: true, scroll: true })}
          />
          <FunnelCaption funnel={funnel.data} />
        </>
      )}
    </ChartCard>
  );
};

const CatalogRow = ({ catalog, selected, onSelect }: { catalog: CatalogStats; selected: boolean; onSelect: () => void }) => {
  const score = Math.round(catalog.avg_health_score);
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        className="group flex w-full cursor-pointer flex-col gap-1 rounded-[var(--radius-md)] px-2 py-2 text-left hover:bg-[var(--color-surface-sunken)] aria-pressed:bg-[var(--color-primary-soft)]"
      >
        <span className="flex items-center gap-2">
          <span
            className={`size-1.5 shrink-0 rounded-full ${catalog.enabled ? "bg-[var(--color-status-success)]" : "bg-muted-foreground/40"}`}
            aria-hidden="true"
          />
          <span className="sr-only">{catalog.enabled ? "Enabled" : "Disabled"}. </span>
          <span className="min-w-0 flex-1 truncate font-medium group-hover:underline">{catalog.catalog_name}</span>
          <Badge variant="outline" className="shrink-0 text-[10px] uppercase">
            {catalog.catalog_type}
          </Badge>
          <span
            className="shrink-0 text-sm font-semibold tabular-nums"
            style={{ color: healthScoreColor(score) }}
            title="Average health score (0–100)"
          >
            {score}
          </span>
        </span>
        <span className="pl-3.5 text-xs tabular-nums text-muted-foreground">
          {formatNumber(catalog.table_count)} tables · {formatGb(catalog.total_size_gb)} ·{" "}
          {formatCurrency(catalog.monthly_cost_usd)}/mo
          {catalog.projected_savings_usd_month ? ` · saves ${formatSavings(catalog.projected_savings_usd_month)}/mo` : ""}
        </span>
      </button>
    </li>
  );
};

const CatalogsCard = ({ data }: { data: MultiCatalogDashboard }) => {
  const { searchParams, setSearchParams } = useExplorerViewParam();
  const selected = new Set((searchParams.get("catalog") ?? "").split(",").filter(Boolean));
  const toggleCatalog = (id: string) => {
    const params = new URLSearchParams(searchParams);
    params.delete("source");
    if (selected.has(id) && selected.size === 1) params.delete("catalog");
    else params.set("catalog", id);
    params.delete("page");
    setSearchParams(params, { replace: true });
    scrollToExplorer();
  };
  const fleetCost =
    data.total_tables && data.total_cost_usd != null ? `${formatCurrency(data.total_cost_usd)}/mo` : null;
  return (
    <ChartCard
      title={data.catalogs.length === 1 ? "Catalog" : `Catalogs · ${data.catalogs.length}`}
      actions={
        <Link to="/cost" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
          Cost <ArrowRight className="size-3" aria-hidden="true" />
        </Link>
      }
    >
      <ul className="-mx-2 flex max-h-[232px] flex-col overflow-y-auto">
        {data.catalogs.map((catalog) => (
          <CatalogRow
            key={catalog.catalog_id}
            catalog={catalog}
            selected={selected.has(catalog.catalog_id)}
            onSelect={() => toggleCatalog(catalog.catalog_id)}
          />
        ))}
      </ul>
      {fleetCost ? (
        <p className="mt-2 border-t border-border pt-2 text-xs text-muted-foreground" title={CATALOG_FLEET_COST_TOOLTIP}>
          Modeled storage cost <span className="font-medium tabular-nums text-foreground">{fleetCost}</span>
        </p>
      ) : null}
    </ChartCard>
  );
};

export const ScannerLagBanner = () => {
  const stale = useStaleScanCount();
  const { activeTotal } = useTriageCounts();
  const count = stale.data ?? 0;
  if (!activeTotal || count < Math.max(1, Math.ceil(activeTotal * SCANNER_LAG_SHARE))) return null;
  return (
    <Alert
      className="mb-4"
      variant="warning"
      title="The scanner is behind"
      description={
        <>
          {formatNumber(count)} of {formatNumber(activeTotal)} active tables have not been scanned in{" "}
          {SCAN_STALE_HOURS} hours, so their health and maintenance decisions use old stats.{" "}
          <Link className="underline underline-offset-2" to="/runs/scanner">
            Open scanner
          </Link>
        </>
      }
    />
  );
};

export const CatalogDashboard = () => {
  const { api } = useAuth();
  const { data, isLoading, error, refetch } = useQuery<MultiCatalogDashboard>({
    queryKey: ["multi-catalog", api.token],
    queryFn: () => getMultiCatalogDashboard(api),
    refetchInterval: 60000
  });

  return (
    <>
      <ScannerLagBanner />
      <TriageCards />
      <section aria-label="Nightly maintenance and catalogs" className="mb-6 grid gap-4 xl:grid-cols-3">
        <FunnelCard />
        <AsyncBoundary
          isLoading={isLoading}
          isError={Boolean(error)}
          error={error}
          onRetry={() => void refetch()}
          skeleton={<Skeleton className="h-[300px] w-full" />}
        >
          {data && data.catalogs.length ? (
            <CatalogsCard data={data} />
          ) : (
            <ChartCard title="Catalogs">
              <p className="text-sm text-muted-foreground">No catalogs connected.</p>
            </ChartCard>
          )}
        </AsyncBoundary>
      </section>
    </>
  );
};
