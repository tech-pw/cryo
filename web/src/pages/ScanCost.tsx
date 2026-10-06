import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getScanCostAttribution } from "../api/client";
import { EmptyState, QueryErrorAlert } from "../components/common";
import { Badge } from "../components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { MetricGrid, MetricTile } from "../components/ui/metric-grid";
import { PageHeader } from "../components/ui/page-header";
import { Select } from "../components/ui/select";
import { Skeleton } from "../components/ui/skeleton";
import { useAuth } from "../lib/auth";
import { formatGb, formatNumber } from "../lib/format";

const DAY_OPTIONS = [
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
  { value: "90", label: "Last 90 days" }
];

export const ScanCost = ({ embedded = false }: { embedded?: boolean }) => {
  const { api } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawDays = searchParams.get("range") ?? "7";
  const days = DAY_OPTIONS.some((option) => option.value === rawDays) ? rawDays : "7";

  const query = useQuery({
    queryKey: ["scan-cost-attribution", days],
    queryFn: () => getScanCostAttribution(api, { days: Number(days) })
  });

  const isLoading = query.isLoading;
  const byClient = query.data?.by_client ?? [];
  const rows = query.data?.rows ?? [];
  const maxClientGb = byClient[0]?.physical_gb_scanned ?? 0;

  return (
    <div className="flex flex-col gap-6">
      {embedded ? (
        <div className="mb-3 flex items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">Bytes scanned, from the query log. This is not a dollar cost.</p>
          <Select
            className="w-40"
            value={days}
            onChange={(value) =>
              setSearchParams((prev) => {
                const next = new URLSearchParams(prev);
                next.set("range", value ?? "7");
                return next;
              })
            }
            options={DAY_OPTIONS}
          />
        </div>
      ) : (
        <PageHeader
          title="Reads"
          subtitle="Bytes scanned per client and schema, from the query log. This is not a dollar cost."
          actions={
          <Select
            className="w-40"
            value={days}
            onChange={(value) =>
              setSearchParams((prev) => {
                const next = new URLSearchParams(prev);
                next.set("range", value ?? "7");
                return next;
              })
            }
            options={DAY_OPTIONS}
          />
          }
        />
      )}

      {query.isError ? <QueryErrorAlert error={query.error} /> : null}

      <MetricGrid columns={2}>
        <MetricTile
          label="Total queries"
          value={isLoading ? undefined : formatNumber(query.data?.total_query_count ?? 0)}
          loading={isLoading}
        />
        <MetricTile
          label="Total scanned"
          value={isLoading ? undefined : formatGb(query.data?.total_physical_gb_scanned ?? 0)}
          emphasis
          loading={isLoading}
        />
      </MetricGrid>

      <Card>
        <CardHeader>
          <CardTitle>Bytes scanned by client</CardTitle>
          <CardDescription>
            Which tool is generating the most read cost — Metabase dashboards, dbt runs, ad-hoc
            SQL clients, and anything else that queried through Trino.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {isLoading ? (
            <Skeleton className="h-48 w-full" />
          ) : byClient.length === 0 ? (
            <EmptyState
              title="No scan data yet"
              description="Cost attribution rolls up once a day — check back after the first scheduled run."
            />
          ) : (
            byClient.map((client) => (
              <div key={client.client} className="flex items-center gap-3">
                <div className="w-40 shrink-0 truncate text-sm text-foreground" title={client.client}>
                  {client.client}
                </div>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-[var(--color-surface-sunken)]">
                  <div
                    className="h-full rounded-full bg-[var(--color-primary)]"
                    style={{
                      width: `${maxClientGb > 0 ? (client.physical_gb_scanned / maxClientGb) * 100 : 0}%`
                    }}
                  />
                </div>
                <span className="w-24 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                  {formatGb(client.physical_gb_scanned)}
                </span>
                <span className="w-20 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                  {formatNumber(client.query_count)} qry
                </span>
              </div>
            ))
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>By client and schema</CardTitle>
          <CardDescription>
            Same data, broken out by which schema each client actually scanned.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : rows.length === 0 ? (
            <EmptyState title="No rows yet" description="Nothing scanned in the selected window." />
          ) : (
            <div className="flex flex-col">
              {rows.map((row) => (
                <div
                  key={`${row.client}-${row.schema_name}`}
                  className="flex items-center justify-between gap-3 border-b border-[var(--color-border)] py-3 last:border-b-0"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-foreground">{row.client}</p>
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">
                      schema: {row.schema_name} &middot; {formatNumber(row.query_count)} queries
                      {row.failed_query_count > 0
                        ? ` · ${formatNumber(row.failed_query_count)} failed`
                        : ""}
                    </p>
                  </div>
                  <Badge variant="secondary" className="tabular-nums shrink-0">
                    {formatGb(row.physical_gb_scanned)}
                  </Badge>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};
