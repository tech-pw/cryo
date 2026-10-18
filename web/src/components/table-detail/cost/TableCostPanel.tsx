import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Database, TrendingDown } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { apiFetch, getCompactionCandidates, getTable, getTableCost, getTableS3Tiering } from "../../../api/client";
import type { components } from "../../../api/openapi";
import { useAuth } from "../../../lib/auth";
import { formatBytesGb, formatCurrency, formatNumber, formatTime } from "../../../lib/format";
import { operationLabels } from "../../../lib/theme";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import { QueryErrorAlert } from "../../common";
import { CostPrecisionBadge } from "../../CostPrecisionBadge";
import { Card } from "../../ui/card";
import { isOrphanScanStale } from "../../../lib/costStaleness";
import { ArchivePanel } from "./ArchivePanel";
import "./table-cost.css";

const TIERING_LABELS: Record<string, string> = {
  enable: "Turn on Intelligent-Tiering",
  not_suitable: "Intelligent-Tiering won't pay off here",
  already_optimal: "Already on the right class",
  unknown: "Not enough access data yet"
};

const numberOrNull = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
const money = (value: unknown) => {
  const amount = numberOrNull(value);
  if (amount == null) return "Not measured";
  if (amount > 0 && amount < 1) {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    }).format(amount);
  }
  return formatCurrency(amount);
};
const isS3Location = (location: string | null | undefined) =>
  /^(s3|s3a|s3n):\/\//i.test(location ?? "");

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
  const location = tableQuery.data?.location;
  const s3Storage = tableQuery.isSuccess && isS3Location(location);
  const tiering = useQuery({
    queryKey: tableKeys.tiering(tableId, token), queryFn: () => getTableS3Tiering(api, tableId),
    enabled: Boolean(tableId) && s3Storage, retry: false
  });
  const archiveSummary = useQuery({
    queryKey: ["archive-summary", tableId, token],
    queryFn: () => apiFetch<components["schemas"]["ArchiveSummaryRead"]>(`/tables/${tableId}/tiering`, api),
    enabled: Boolean(tableId) && s3Storage, retry: false
  });
  const compaction = useQuery({
    queryKey: tableKeys.compactionCandidates(tableId, token),
    queryFn: () => getCompactionCandidates(api, tableId, { horizonDays: 30, k: 10 }),
    enabled: Boolean(tableId), retry: false
  });

  const cost = costQuery.isSuccess ? costQuery.data : undefined;
  const storageDollars = s3Storage ? money(cost?.current_monthly_cost_usd) : "Not measured";
  const modelledSavings = s3Storage ? money(cost?.projected_savings_usd_month) : "Not measured";
  const heuristic = cost?.precision === "heuristic";
  const parts = [
    { name: "Live data bytes", detail: "Data files referenced by the table", value: numberOrNull(cost?.live_bytes), tone: "live" },
    {
      name: "Dead bytes",
      detail: "Historical files retained by snapshots",
      value: heuristic ? null : numberOrNull(cost?.dead_bytes),
      tone: "dead"
    },
    { name: "Metadata bytes", detail: "Table metadata and manifests", value: numberOrNull(cost?.metadata_bytes), tone: "metadata" },
    { name: "Orphan bytes", detail: "Storage files outside table metadata", value: numberOrNull(cost?.orphan_bytes), tone: "orphan" }
  ];
  const maxBytes = Math.max(0, ...parts.map((part) => part.value ?? 0));
  const operations = [...(cost?.by_operation ?? [])].sort((a, b) => (b.savings_usd_month ?? 0) - (a.savings_usd_month ?? 0));
  const maxSavings = Math.max(0, ...operations.map((item) => numberOrNull(item.savings_usd_month) ?? 0));
  const candidates = compaction.isSuccess ? compaction.data.candidates : [];
  const candidateValues = candidates.map((row) => numberOrNull(row.savings_estimate_usd));
  const compactionTotal = candidates.length > 0 && candidateValues.every((value) => value != null)
    ? candidateValues.reduce<number>((sum, value) => sum + (value ?? 0), 0) : null;
  const requestCounts = ([
    ["GET/SELECT", cost?.get_requests_30d],
    ["PUT/COPY/POST", cost?.put_requests_30d],
    ["LIST", cost?.list_requests_30d],
    ["HEAD", cost?.head_requests_30d],
    ["DELETE", cost?.delete_requests_30d]
  ] as const).flatMap(([name, count]) => {
    const value = numberOrNull(count);
    return value == null ? [] : [[name, value] as const];
  });
  const requestPricingApproximate = cost?.request_cost_pricing_source &&
    !["live", "aws_price_list_api"].includes(cost.request_cost_pricing_source);
  const requestCaption = cost?.request_cost_usd_month == null
    ? "Request logs have not been recorded"
    : requestPricingApproximate
      ? "Recorded requests × default pricing"
      : "Recorded requests × applicable pricing";

  return (
    <div className="td-tab-content td-cost-workspace">
      <CostCard id="cost-requests" title="What this table costs"
        description={s3Storage
          ? "Storage at the S3 Standard rate, plus the last 30 days of recorded S3 requests."
          : "The configured storage rate is AWS S3 Standard, not this table's store. Storage dollars are not measured here."}
        className="td-cost-summary-card">
        {costQuery.isLoading ? <p className="td-cost-muted">Loading cost…</p> : null}
        <QueryErrorAlert error={costQuery.error} title="Failed to load table cost" />
        {costQuery.isSuccess ? (
          <>
            <dl className="td-cost-kpis">
              <div><dt><Database size={15} aria-hidden /> Estimated storage / month</dt><dd>{storageDollars}</dd><p>{s3Storage ? "At the AWS S3 Standard rate" : "Not this table's store"}</p></div>
              <div><dt>S3 request cost · 30 days</dt><dd>{money(cost?.request_cost_usd_month)}</dd><p>{requestCaption}</p></div>
              <div className="td-cost-opportunity"><dt><TrendingDown size={15} aria-hidden /> Maintenance can save / month</dt><dd>{modelledSavings}</dd><p>{s3Storage ? "Modelled, by removing files no snapshot needs" : "Not measured for this storage"}</p></div>
            </dl>
            <div className="td-cost-evidence">
              <CostPrecisionBadge precision={cost?.precision} lastFullCostScanAt={cost?.last_full_cost_scan_at}
                orphanScanStale={isOrphanScanStale(cost?.precision, cost?.last_full_cost_scan_at)} />
              <span>Cost scan {cost?.scan_at ? formatTime(cost.scan_at) : "not recorded"}</span>
            </div>
            {requestCounts.length ? (
              <dl className="td-cost-request-counts" aria-label="Recorded S3 requests in the last 30 days">
                {requestCounts.map(([name, count]) => (
                  <div key={name}><dt>{name}</dt><dd>{formatNumber(count)}</dd></div>
                ))}
              </dl>
            ) : null}
          </>
        ) : null}
      </CostCard>

      <CostCard id="cost-storage" title="Where storage accumulates" className="td-cost-storage-card"
        description={cost?.orphan_bytes == null
          ? "Bytes by kind, scaled to the largest. Orphan files appear after a listing scan."
          : "Bytes by kind, scaled to the largest."}>
        {costQuery.isLoading || tableQuery.isLoading ? <p className="td-cost-muted">Loading storage parts…</p> : null}
        <QueryErrorAlert error={tableQuery.error} title="Couldn't load table stats" />
        <dl className="td-cost-bars">
          {parts.map((part) => (
            <div key={part.name} data-tone={part.tone}>
              <dt>{part.name}<span>{part.detail}</span></dt><dd>{bytes(part.value)}</dd>
              <div className="td-cost-track" aria-hidden="true"><span style={{ width: maxBytes > 0 && part.value != null ? `${part.value / maxBytes * 100}%` : "0%" }} /></div>
            </div>
          ))}
        </dl>
      </CostCard>

      <CostCard id="cost-savings" title="Ways to save"
        description="Three levers, each estimated on its own. Reclaiming and archiving can touch the same files, so the figures don't add up to one total."
        className="td-cost-levers-card">
        <div className="td-cost-levers">
          <section aria-labelledby="lever-reclaim">
            <h4 id="lever-reclaim">Reclaim with maintenance</h4>
            {costQuery.isLoading ? <p className="td-cost-muted">Loading…</p> : null}
            {costQuery.isError ? <p className="td-cost-muted">Unavailable until table cost loads.</p> : null}
            {costQuery.isSuccess ? <>
              <p className="td-cost-secondary-value">{bytes(cost?.reclaimable_bytes)}<span>reclaimable · {modelledSavings} / month</span></p>
              {operations.length ? <dl className="td-cost-bars td-cost-savings-bars">
                {operations.map((item) => (
                  <div key={item.operation}>
                    <dt>{operationLabels[item.operation] ?? item.operation}<span>{bytes(item.reclaimable_bytes)}</span></dt>
                    <dd>{money(item.savings_usd_month)}</dd>
                    <div className="td-cost-track" aria-hidden="true"><span style={{ width: maxSavings > 0 && numberOrNull(item.savings_usd_month) != null ? `${item.savings_usd_month / maxSavings * 100}%` : "0%" }} /></div>
                  </div>
                ))}
              </dl> : <p className="td-cost-empty">{(cost?.reclaimable_bytes ?? 0) > 0
                ? "The cost scan didn't record which operation frees what."
                : "Nothing to reclaim right now."}</p>}
            </> : null}
            <Link className="td-cost-link" to={`/tables/${tableId}?tab=maintenance`}>Review maintenance <ArrowRight size={15} aria-hidden /></Link>
          </section>

          <section aria-labelledby="lever-tier">
            <h4 id="lever-tier">Cheaper storage class</h4>
            {tableQuery.isSuccess && !s3Storage ? <p className="td-cost-muted">Not available for this storage</p> : null}
            {s3Storage ? <>
              <p className="td-cost-secondary-value">
                {archiveSummary.data?.read_evidence === "trusted" ? money(archiveSummary.data.projected_savings_usd_month) : "Not estimated"}
                <span>/ month from archiving cold partitions</span>
              </p>
              <QueryErrorAlert error={tiering.error} title="Failed to load S3 tiering" />
              {tiering.isSuccess ? (
                <div className="td-cost-tier-advice" data-recommend={tiering.data.recommendation === "enable" || undefined}>
                  <strong>{TIERING_LABELS[tiering.data.recommendation] ?? "Intelligent-Tiering"}</strong>
                  <span>{tiering.data.reason}</span>
                  {tiering.data.recommendation === "enable" && numberOrNull(tiering.data.estimated_annual_savings_usd) != null
                    ? <span>About {money((tiering.data.estimated_annual_savings_usd ?? 0) / 12)} / month for the whole table.</span>
                    : null}
                </div>
              ) : null}
              <a className="td-cost-link" href="#archive">Choose partitions to archive <ArrowRight size={15} aria-hidden /></a>
            </> : null}
          </section>

          <section aria-labelledby="lever-compaction">
            <h4 id="lever-compaction">Cheaper queries from compaction</h4>
            <QueryErrorAlert error={compaction.error} title="Couldn't load compaction savings" />
            {compaction.isLoading ? <p className="td-cost-muted">Loading…</p> : null}
            {compaction.isSuccess ? (candidates.length
              ? <p className="td-cost-secondary-value">{money(compactionTotal)}<span>over 30 days · top {candidates.length} partitions</span></p>
              : <p className="td-cost-empty">No partitions worth compacting for query cost.</p>) : null}
            <p className="td-cost-muted">Fewer, larger files mean fewer S3 reads and less engine time per query.</p>
            <Link to={`/tables/${tableId}?tab=maintenance#maintenance-compaction-horizon`} className="td-cost-link">Open compaction ranking <ArrowRight size={15} aria-hidden /></Link>
          </section>
        </div>
      </CostCard>

      {s3Storage ? <ArchivePanel tableId={tableId} /> : null}
    </div>
  );
};
