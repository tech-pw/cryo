import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getCompactionCandidates, getTable, getTableCost, getTableS3Tiering } from "../../../api/client";
import type { TableCostRead } from "../../../api/types";
import { useAuth } from "../../../lib/auth";
import { formatBytesGb, formatCurrency } from "../../../lib/format";
import { operationLabels } from "../../../lib/theme";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import { QueryErrorAlert } from "../../common";
import { ChartCard } from "../ChartCard";
import { RequestCostShare } from "../RequestCostShare";

const numberOrNull = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const moneyText = (kind: "Estimated" | "Measured" | "Modelled", value: number | null): string =>
  value == null ? "Not measured" : `${kind} ${formatCurrency(value)}`;

const byteText = (value: number | null): string => (value == null ? "Not measured" : formatBytesGb(value));

const deleteFileBytes = (raw: Record<string, unknown> | null | undefined): number | null => {
  if (!raw) return null;
  return numberOrNull(raw.delete_bytes) ?? numberOrNull(raw.total_delete_bytes);
};

export const TableCostPanel = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const costQuery = useQuery({
    queryKey: tableKeys.cost(tableId, token),
    queryFn: () => getTableCost(api, tableId),
    enabled: Boolean(tableId),
    retry: false
  });
  const tableQuery = useQuery({
    queryKey: tableKeys.detail(tableId, token),
    queryFn: () => getTable(api, tableId),
    enabled: Boolean(tableId),
    retry: false
  });
  const tiering = useQuery({
    queryKey: tableKeys.tiering(tableId, token),
    queryFn: () => getTableS3Tiering(api, tableId),
    enabled: Boolean(tableId),
    retry: false
  });
  const compaction = useQuery({
    queryKey: ["compaction-candidates", tableId],
    queryFn: () => getCompactionCandidates(api, tableId, { horizonDays: 30, k: 10 }),
    enabled: Boolean(tableId),
    retry: false
  });

  const cost: TableCostRead | undefined = costQuery.isSuccess ? costQuery.data : undefined;
  const estimatedStorage = cost ? numberOrNull(cost.current_monthly_cost_usd) : null;
  const measuredRequests = cost ? numberOrNull(cost.request_cost_usd_month) : null;
  const parts = [
    { name: "Live data bytes", bytes: cost ? numberOrNull(cost.live_bytes) : null },
    { name: "Dead bytes", bytes: cost ? numberOrNull(cost.dead_bytes) : null },
    { name: "Metadata bytes", bytes: cost ? numberOrNull(cost.metadata_bytes) : null },
    { name: "Orphan bytes", bytes: cost ? numberOrNull(cost.orphan_bytes) : null },
    { name: "Delete-file bytes", bytes: tableQuery.isSuccess ? deleteFileBytes(tableQuery.data.stats?.raw ?? null) : null }
  ];
  const complete = parts.every((part) => part.bytes != null);
  const totalBytes = parts.reduce((sum, part) => sum + (part.bytes ?? 0), 0);
  const showStack = complete && totalBytes > 0;
  const ranked = [...parts].sort((a, b) => {
    if (a.bytes == null && b.bytes == null) return a.name.localeCompare(b.name);
    if (a.bytes == null) return 1;
    if (b.bytes == null) return -1;
    return b.bytes - a.bytes;
  });
  const modelledOps = cost?.by_operation ?? [];
  const modelledTotal = cost ? numberOrNull(cost.projected_savings_usd_month) : null;
  const compactionSavings = compaction.isSuccess
    ? compaction.data.candidates
        .map((row) => numberOrNull(row.savings_estimate_usd))
        .filter((value): value is number => value != null)
    : [];
  const compactionTotal =
    compaction.isSuccess && compaction.data.candidates.length > 0 && compactionSavings.length > 0
      ? compactionSavings.reduce((sum, value) => sum + value, 0)
      : null;

  return (
    <div className="td-tab-content flex flex-col gap-4">
      <section id="cost-requests" aria-label="estimated storage $ vs measured request $ (30 d)">
        <ChartCard title="estimated storage $ vs measured request $ (30 d)">
          {costQuery.isLoading ? <p className="text-sm text-muted-foreground">Loading cost…</p> : null}
          {costQuery.isError ? (
            <QueryErrorAlert error={costQuery.error} title="Failed to load table cost" />
          ) : null}
          {!costQuery.isLoading ? (
            <>
              <p className="text-sm">Estimated storage {estimatedStorage == null ? "Not measured" : formatCurrency(estimatedStorage)}</p>
              <p className="text-sm">
                Measured request $ (30 d) {measuredRequests == null ? "Not measured" : formatCurrency(measuredRequests)}
              </p>
            </>
          ) : null}
          <RequestCostShare
            storageCostUsd={estimatedStorage}
            requestCostUsd={measuredRequests}
            getRequests30d={cost?.get_requests_30d}
            putRequests30d={cost?.put_requests_30d}
            listRequests30d={cost?.list_requests_30d}
            pricingSource={cost?.request_cost_pricing_source}
          />
        </ChartCard>
      </section>

      <section id="cost-storage" aria-label="Storage parts">
        <ChartCard title="Storage parts">
          {costQuery.isLoading || tableQuery.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading storage parts…</p>
          ) : null}
          {showStack ? (
            <div className="mb-3">
              <p className="mb-1 text-xs text-muted-foreground">These parts are disjoint and sum to 100%.</p>
              <div className="flex h-3 overflow-hidden rounded-full" aria-hidden>
                {parts.map((part) => (
                  <div
                    key={part.name}
                    style={{ width: `${((part.bytes ?? 0) / totalBytes) * 100}%` }}
                    className="h-full bg-primary/70"
                  />
                ))}
              </div>
            </div>
          ) : null}
          {!costQuery.isLoading && !tableQuery.isLoading ? (
          <dl className="m-0 grid gap-1 text-sm">
            {ranked.map((part) => (
              <div key={part.name} className="flex items-baseline justify-between gap-3">
                <dt>{part.name}</dt>
                <dd className="m-0 tabular-nums">{byteText(part.bytes)}</dd>
              </div>
            ))}
          </dl>
          ) : null}
        </ChartCard>
      </section>

      <section id="cost-savings" aria-label="Modelled savings">
        <ChartCard title="Modelled">
          {costQuery.isLoading ? <p className="text-sm text-muted-foreground">Loading modelled savings…</p> : null}
          {!costQuery.isLoading ? <p className="text-sm">{moneyText("Modelled", modelledTotal)}</p> : null}
          {!costQuery.isLoading && modelledOps.length ? (
            <ul className="mt-2 m-0 list-none space-y-1 p-0 text-sm">
              {modelledOps.map((item) => (
                <li key={item.operation}>
                  {operationLabels[item.operation] ?? item.operation}: {moneyText("Modelled", numberOrNull(item.savings_usd_month))}
                </li>
              ))}
            </ul>
          ) : !costQuery.isLoading ? (
            <p className="mt-2 text-sm text-muted-foreground">Not measured</p>
          ) : null}
        </ChartCard>
      </section>

      <section id="cost-tiering" aria-label="S3 tiering">
        <ChartCard title="S3 tiering">
          {tiering.isError ? (
            <QueryErrorAlert error={tiering.error} title="Failed to load S3 tiering" />
          ) : null}
          {tiering.isSuccess ? (
            <div className="text-sm">
              <p>{tiering.data.recommendation}</p>
              <p className="text-muted-foreground">{tiering.data.reason}</p>
              <p>{moneyText("Estimated", numberOrNull(tiering.data.estimated_annual_savings_usd))} annual savings</p>
            </div>
          ) : tiering.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading tiering…</p>
          ) : (
            <p className="text-sm">Not measured</p>
          )}
        </ChartCard>
      </section>

      <section id="cost-compaction" aria-label="Compaction savings">
        <ChartCard title="Compaction">
          <p className="text-sm">Modelled total {compactionTotal == null ? "Not measured" : formatCurrency(compactionTotal)}</p>
          <Link
            to={`/tables/${tableId}?tab=maintenance#maintenance-compaction`}
            className="mt-2 inline-block text-sm font-medium text-primary hover:underline"
          >
            Open compaction ranking
          </Link>
        </ChartCard>
      </section>
    </div>
  );
};
