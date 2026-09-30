import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Database, TrendingDown } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { getCompactionCandidates, getTable, getTableCost, getTableS3Tiering } from "../../../api/client";
import { useAuth } from "../../../lib/auth";
import { formatBytesGb, formatCurrency, formatNumber, formatTime } from "../../../lib/format";
import { operationLabels } from "../../../lib/theme";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import { QueryErrorAlert } from "../../common";
import { CostPrecisionBadge } from "../../CostPrecisionBadge";
import { Card } from "../../ui/card";
import { isOrphanScanStale } from "../../../lib/costStaleness";
import "./table-cost.css";

const numberOrNull = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
const money = (value: unknown) => {
  const amount = numberOrNull(value);
  return amount == null ? "Not measured" : formatCurrency(amount);
};
const bytes = (value: unknown) => {
  const amount = numberOrNull(value);
  return amount == null ? "Not measured" : formatBytesGb(amount);
};

const CostCard = ({ id, title, description, children, className = "" }: {
  id: string; title: string; description: string; children: ReactNode; className?: string;
}) => (
  <Card id={id} role="region" aria-labelledby={`${id}-heading`} className={`td-cost-card ${className}`}>
    <header>
      <h3 id={`${id}-heading`}>{title}</h3>
      <p>{description}</p>
    </header>
    {children}
  </Card>
);

export const TableCostPanel = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const costQuery = useQuery({
    queryKey: tableKeys.cost(tableId, token), queryFn: () => getTableCost(api, tableId),
    enabled: Boolean(tableId), retry: false
  });
  const tableQuery = useQuery({
    queryKey: tableKeys.detail(tableId, token), queryFn: () => getTable(api, tableId),
    enabled: Boolean(tableId), retry: false
  });
  const tiering = useQuery({
    queryKey: tableKeys.tiering(tableId, token), queryFn: () => getTableS3Tiering(api, tableId),
    enabled: Boolean(tableId), retry: false
  });
  const compaction = useQuery({
    queryKey: tableKeys.compactionCandidates(tableId, token),
    queryFn: () => getCompactionCandidates(api, tableId, { horizonDays: 30, k: 10 }),
    enabled: Boolean(tableId), retry: false
  });

  const cost = costQuery.isSuccess ? costQuery.data : undefined;
  const raw = tableQuery.isSuccess ? tableQuery.data?.stats?.raw : undefined;
  const parts = [
    { name: "Live data bytes", detail: "Data files referenced by the table", value: numberOrNull(cost?.live_bytes), tone: "live" },
    { name: "Dead bytes", detail: "Historical files retained by snapshots", value: numberOrNull(cost?.dead_bytes), tone: "dead" },
    { name: "Metadata bytes", detail: "Table metadata and manifests", value: numberOrNull(cost?.metadata_bytes), tone: "metadata" },
    { name: "Orphan bytes", detail: "Storage files outside table metadata", value: numberOrNull(cost?.orphan_bytes), tone: "orphan" },
    { name: "Delete-file bytes", detail: "From the latest table scan", value: numberOrNull(raw?.delete_bytes) ?? numberOrNull(raw?.total_delete_bytes), tone: "delete" }
  ];
  const maxBytes = Math.max(0, ...parts.map((part) => part.value ?? 0));
  const operations = [...(cost?.by_operation ?? [])].sort((a, b) => (b.savings_usd_month ?? 0) - (a.savings_usd_month ?? 0));
  const maxSavings = Math.max(0, ...operations.map((item) => numberOrNull(item.savings_usd_month) ?? 0));
  const candidates = compaction.isSuccess ? compaction.data.candidates : [];
  const candidateValues = candidates.map((row) => numberOrNull(row.savings_estimate_usd));
  const compactionTotal = candidates.length > 0 && candidateValues.every((value) => value != null)
    ? candidateValues.reduce<number>((sum, value) => sum + (value ?? 0), 0) : null;
  const requestPricingApproximate = cost?.request_cost_pricing_source &&
    !["live", "aws_price_list_api"].includes(cost.request_cost_pricing_source);

  return (
    <div className="td-tab-content td-cost-workspace">
      <CostCard id="cost-requests" title="What this table costs"
        description="Storage is a monthly estimate. Request cost uses recorded S3 activity from the last 30 days."
        className="td-cost-summary-card">
        {costQuery.isLoading ? <p className="td-cost-muted">Loading cost…</p> : null}
        <QueryErrorAlert error={costQuery.error} title="Failed to load table cost" />
        {costQuery.isSuccess ? (
          <>
            <dl className="td-cost-kpis">
              <div><dt><Database size={15} aria-hidden /> Estimated storage / month</dt><dd>{money(cost?.current_monthly_cost_usd)}</dd><p>At the configured storage rate</p></div>
              <div><dt>S3 request cost · 30 days</dt><dd>{money(cost?.request_cost_usd_month)}</dd><p>{requestPricingApproximate ? "Recorded requests × default pricing" : "Recorded requests × applicable pricing"}</p></div>
              <div className="td-cost-opportunity"><dt><TrendingDown size={15} aria-hidden /> Modelled savings / month</dt><dd>{money(cost?.projected_savings_usd_month)}</dd><p>Potential reduction, before execution costs</p></div>
            </dl>
            <div className="td-cost-evidence">
              <CostPrecisionBadge precision={cost?.precision} lastFullCostScanAt={cost?.last_full_cost_scan_at}
                orphanScanStale={isOrphanScanStale(cost?.precision, cost?.last_full_cost_scan_at)} />
              <span>Cost scan {cost?.scan_at ? formatTime(cost.scan_at) : "not recorded"}</span>
            </div>
            <dl className="td-cost-request-counts" aria-label="Recorded S3 requests in the last 30 days">
              {[["GET", cost?.get_requests_30d], ["PUT", cost?.put_requests_30d], ["LIST", cost?.list_requests_30d]].map(([name, count]) => (
                <div key={String(name)}><dt>{name}</dt><dd>{numberOrNull(count) == null ? "Not measured" : formatNumber(count as number)}</dd></div>
              ))}
            </dl>
          </>
        ) : null}
      </CostCard>

      <CostCard id="cost-storage" title="Where storage accumulates"
        description="Compare recorded byte counts. The delete-file measure comes from a separate scan; these bars are not shares of a total.">
        {costQuery.isLoading || tableQuery.isLoading ? <p className="td-cost-muted">Loading storage parts…</p> : null}
        <QueryErrorAlert error={tableQuery.error} title="Couldn't load delete-file storage" />
        <dl className="td-cost-bars">
          {parts.map((part) => (
            <div key={part.name} data-tone={part.tone}>
              <dt>{part.name}<span>{part.detail}</span></dt><dd>{bytes(part.value)}</dd>
              <div className="td-cost-track" aria-hidden="true"><span style={{ width: maxBytes > 0 && part.value != null ? `${part.value / maxBytes * 100}%` : "0%" }} /></div>
            </div>
          ))}
        </dl>
      </CostCard>

      <CostCard id="cost-savings" title="Where maintenance could help"
        description="Modelled monthly savings by operation. A preview checks retention rules and execution guardrails before any action.">
        {costQuery.isLoading ? <p className="td-cost-muted">Loading modelled savings…</p> : null}
        {costQuery.isError ? <p className="td-cost-muted">Savings are unavailable until table cost can be loaded.</p> : null}
        {costQuery.isSuccess ? (
          <>
            <dl className="td-cost-projection"><div><dt>Projected storage / month</dt><dd>{money(cost?.projected_monthly_cost_usd)}</dd></div>
              <div><dt>Potentially reclaimable</dt><dd>{bytes(cost?.reclaimable_bytes)}</dd></div></dl>
            {operations.length ? <dl className="td-cost-bars td-cost-savings-bars">
              {operations.map((item) => (
                <div key={item.operation}>
                  <dt>{operationLabels[item.operation] ?? item.operation}<span>{bytes(item.reclaimable_bytes)} potentially reclaimable</span></dt>
                  <dd>{money(item.savings_usd_month)}</dd>
                  <div className="td-cost-track" aria-hidden="true"><span style={{ width: maxSavings > 0 && numberOrNull(item.savings_usd_month) != null ? `${item.savings_usd_month / maxSavings * 100}%` : "0%" }} /></div>
                </div>
              ))}
            </dl> : <p className="td-cost-empty">No operation-level savings recorded. A deeper cost scan may reveal opportunities.</p>}
          </>
        ) : null}
        <Link className="td-cost-link" to={`/tables/${tableId}?tab=maintenance`}>Review maintenance <ArrowRight size={15} aria-hidden /></Link>
      </CostCard>

      <CostCard id="cost-tiering" title="Storage tiering" description="A separate opportunity to reduce the cost of colder objects.">
        <QueryErrorAlert error={tiering.error} title="Failed to load S3 tiering" />
        {tiering.isLoading ? <p className="td-cost-muted">Loading tiering…</p> : null}
        {tiering.isSuccess ? <>
          <p className="td-cost-recommendation">{tiering.data.recommendation}</p>
          <p className="td-cost-muted">{tiering.data.reason}</p>
          <p className="td-cost-secondary-value">{money(tiering.data.estimated_annual_savings_usd)}<span>estimated savings / year</span></p>
          <p className="td-cost-footnote">Annual estimate; not added to the monthly maintenance savings above.</p>
        </> : null}
      </CostCard>

      <CostCard id="cost-compaction" title="Compaction economics" description="The 30-day model for up to 10 ranked partition candidates.">
        <QueryErrorAlert error={compaction.error} title="Couldn't load compaction savings" />
        {compaction.isLoading ? <p className="td-cost-muted">Loading compaction estimate…</p> : null}
        {compaction.isSuccess ? <>
          {candidates.length ? <p className="td-cost-secondary-value">{money(compactionTotal)}<span>modelled across {candidates.length} candidates</span></p>
            : <p className="td-cost-empty">No compaction candidates returned for this horizon.</p>}
          <p className="td-cost-footnote">This model has a different scope from storage savings. Do not add the estimates together.</p>
        </> : null}
        <Link to={`/tables/${tableId}?tab=maintenance#maintenance-compaction`} className="td-cost-link">Open compaction ranking <ArrowRight size={15} aria-hidden /></Link>
      </CostCard>
    </div>
  );
};
