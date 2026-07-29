import { useMutation, useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
  explainTableHealth,
  getAnomalyRootCauseBrief,
  getAutoCompCompare,
  getDataQualityProfile,
  getSimilarTables,
  getTableAnomalies,
  getTableHealthClassification,
  getTablePiiClassification,
  recordSkillsetOutcome
} from "../api/client";
import type { SimilarTableRead, UUID } from "../api/types";
import { EmptyState } from "../components/common";
import { useAuth } from "../lib/auth";
import { schemaTableName } from "../lib/tableLabels";
import { formatTime } from "../lib/format";
import { Alert } from "./ui/alert";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardContent } from "./ui/card";
import { DataTable } from "./ui/data-table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";

interface TableAiInsightsProps {
  tableId: UUID;
  lastSnapshotCommittedAt?: string | null;
  embedded?: boolean;
  visibleTabs?: Array<"pii" | "similar" | "risk" | "anomalies" | "dq" | "autocomp">;
  hideTabsWhenSingle?: boolean;
}

const similarColumns: ColumnDef<SimilarTableRead, unknown>[] = [
  {
    id: "name",
    header: "Table",
    cell: ({ row }) => (
      <Link to={`/tables/${row.original.table_id}`} className="truncate">
        {schemaTableName(row.original)}
      </Link>
    )
  },
  {
    accessorKey: "similarity",
    header: "Match",
    cell: ({ getValue }) => <span className="block text-right">{`${((getValue() as number) * 100).toFixed(0)}%`}</span>
  }
];

export const TableAiInsights = ({
  tableId,
  lastSnapshotCommittedAt,
  embedded = false,
  visibleTabs,
  hideTabsWhenSingle = false
}: TableAiInsightsProps) => {
  const { api } = useAuth();

  const pii = useQuery({ queryKey: ["table-pii", tableId], queryFn: () => getTablePiiClassification(api, tableId), retry: false });
  const similar = useQuery({ queryKey: ["table-similar", tableId], queryFn: () => getSimilarTables(api, tableId), retry: false });
  const health = useQuery({ queryKey: ["table-health-classification", tableId], queryFn: () => getTableHealthClassification(api, tableId), retry: false });
  const anomalies = useQuery({ queryKey: ["table-anomalies", tableId], queryFn: () => getTableAnomalies(api, tableId), retry: false });
  const anomalyBrief = useQuery({ queryKey: ["table-anomaly-brief", tableId], queryFn: () => getAnomalyRootCauseBrief(api, tableId), retry: false });
  const dataQuality = useQuery({ queryKey: ["table-data-quality", tableId], queryFn: () => getDataQualityProfile(api, tableId), retry: false });
  const autocomp = useQuery({ queryKey: ["table-autocomp", tableId], queryFn: () => getAutoCompCompare(api, tableId), retry: false });

  const explain = useMutation({
    mutationFn: () => explainTableHealth(api, tableId),
    onSuccess: (result) => toast.info(result.explanation, { duration: 8000 }),
    onError: (error) => toast.error((error as Error).message)
  });
  const anomalyFeedback = useMutation({
    mutationFn: (decision: "accepted" | "rejected") =>
      recordSkillsetOutcome(api, "iceberg_advisor", {
        prompt_name: "anomaly_root_cause_brief",
        recommendation: anomalyBrief.data?.hypotheses?.[0]?.title ?? "No anomaly hypothesis available",
        decision,
        table_id: tableId
      }),
    onSuccess: () => toast.success("Feedback recorded"),
    onError: (error) => toast.error((error as Error).message)
  });

  const loading = pii.isLoading || similar.isLoading || health.isLoading || anomalies.isLoading || dataQuality.isLoading;

  const tabDefs = [
    {
      key: "pii" as const,
      label: "PII",
      content: (pii.data?.matches ?? []).length ? (
        <DataTable
          columns={[
            { accessorKey: "column_name", header: "Column" },
            { accessorKey: "pii_category", header: "Category", cell: ({ getValue }) => <Badge>{String(getValue())}</Badge> },
            { accessorKey: "similarity", header: "Score", cell: ({ getValue }) => <span className="text-right">{(getValue() as number).toFixed(2)}</span> },
            {
              accessorKey: "already_tagged",
              header: "Tagged",
              cell: ({ getValue }) => <Badge variant={(getValue() as boolean) ? "success" : "secondary"}>{(getValue() as boolean) ? "yes" : "no"}</Badge>
            }
          ]}
          data={pii.data?.matches ?? []}
          getRowId={(row) => row.column_name}
          density="compact"
        />
      ) : (
        <EmptyState title="No PII signals" description="No likely PII columns from metadata embeddings." />
      )
    },
    {
      key: "similar" as const,
      label: "Similar tables",
      content: (similar.data?.similar ?? []).length ? (
        <DataTable columns={similarColumns} data={similar.data?.similar ?? []} getRowId={(row) => row.table_id} density="compact" />
      ) : (
        <EmptyState title="No matches" description="No fleet tables above the similarity threshold." />
      )
    },
    {
      key: "risk" as const,
      label: "Compaction risk",
      content: health.data ? (
        <div className="space-y-1">
          <Badge variant={health.data.needs_emergency_compaction ? "danger" : "success"}>
            {health.data.needs_emergency_compaction ? "Elevated emergency risk" : "Normal"}
          </Badge>
          <p className="text-xs text-muted-foreground">
            {(health.data.probability * 100).toFixed(0)}% probability · {health.data.method}
          </p>
        </div>
      ) : (
        <EmptyState title="Unavailable" description="Classifier needs table stats and fleet examples." />
      )
    },
    {
      key: "anomalies" as const,
      label: `Anomalies${anomalies.data?.anomalies.length ? ` (${anomalies.data.anomalies.length})` : ""}`,
      content: (
        <div className="space-y-3">
          {anomalyBrief.data ? (
            <Alert
              variant="info"
              title="Root-cause brief"
              description={
                <div className="space-y-2">
                  {anomalyBrief.data.hypotheses.map((hypothesis) => (
                    <div key={hypothesis.title}>
                      <p className="font-medium">
                        {hypothesis.title} ({Math.round(hypothesis.confidence * 100)}%)
                      </p>
                      <p className="text-xs text-muted-foreground">{hypothesis.because}</p>
                    </div>
                  ))}
                  {anomalyBrief.data.evidence.length ? (
                    <p className="text-xs text-muted-foreground">Evidence: {anomalyBrief.data.evidence.join(" | ")}</p>
                  ) : null}
                  <div className="flex gap-2">
                    <Button size="sm" loading={anomalyFeedback.isPending} onClick={() => anomalyFeedback.mutate("accepted")}>
                      Helpful
                    </Button>
                    <Button size="sm" loading={anomalyFeedback.isPending} onClick={() => anomalyFeedback.mutate("rejected")}>
                      Not helpful
                    </Button>
                  </div>
                </div>
              }
            />
          ) : null}
          {(anomalies.data?.anomalies ?? []).length ? (
            <DataTable
              columns={[
                { accessorKey: "metric", header: "Metric" },
                { accessorKey: "value", header: "Value", cell: ({ getValue }) => <span className="text-right">{(getValue() as number).toLocaleString(undefined, { maximumFractionDigits: 2 })}</span> },
                { accessorKey: "baseline", header: "Baseline", cell: ({ getValue }) => <span className="text-right">{(getValue() as number).toLocaleString(undefined, { maximumFractionDigits: 2 })}</span> },
                { accessorKey: "z_score", header: "Z", cell: ({ getValue }) => <span className="text-right">{(getValue() as number).toFixed(2)}</span> },
                {
                  accessorKey: "severity",
                  header: "Severity",
                  cell: ({ getValue }) => {
                    const v = getValue() as string;
                    return <Badge variant={v === "critical" ? "danger" : v === "warning" ? "warning" : "secondary"}>{v}</Badge>;
                  }
                }
              ]}
              data={anomalies.data?.anomalies ?? []}
              getRowId={(row) => `${row.metric}-${row.z_score}`}
              density="compact"
            />
          ) : (
            <EmptyState title="No anomalies" description="Stats history looks stable vs recent baseline." />
          )}
        </div>
      )
    },
    {
      key: "dq" as const,
      label: "Data quality",
      content: dataQuality.data ? (
        <div className="space-y-3">
          {(dataQuality.data.new_columns.length > 0 || dataQuality.data.dropped_columns.length > 0) && (
            <Alert
              variant="warning"
              title="Schema drift detected"
              description={
                <div>
                  {dataQuality.data.new_columns.length ? <p>New: {dataQuality.data.new_columns.join(", ")}</p> : null}
                  {dataQuality.data.dropped_columns.length ? <p>Dropped: {dataQuality.data.dropped_columns.join(", ")}</p> : null}
                </div>
              }
            />
          )}
          <p className="text-xs text-muted-foreground">
            Profiled {formatTime(dataQuality.data.profiled_at)} · {dataQuality.data.anomalies_detected} column anomalies
          </p>
          <DataTable
            columns={[
              { accessorKey: "column_name", header: "Column" },
              {
                accessorKey: "null_rate_pct",
                header: "Null %",
                cell: ({ row, getValue }) => (
                  <span className="flex items-center justify-end gap-1">
                    {(getValue() as number).toFixed(1)}
                    {row.original.null_rate_spike ? <Badge variant="warning">spike</Badge> : null}
                  </span>
                )
              },
              {
                accessorKey: "null_rate_baseline_pct",
                header: "Baseline null %",
                cell: ({ getValue }) => {
                  const v = getValue() as number | null;
                  return <span className="text-right">{v != null ? v.toFixed(1) : "—"}</span>;
                }
              },
              {
                id: "range",
                header: "Range",
                cell: ({ row }) =>
                  row.original.min_value != null || row.original.max_value != null
                    ? `${row.original.min_value ?? "?"} – ${row.original.max_value ?? "?"}`
                    : "—"
              }
            ]}
            data={dataQuality.data.column_metrics}
            getRowId={(row) => row.column_name}
            density="compact"
          />
        </div>
      ) : dataQuality.isError ? (
        <EmptyState title="No quality profile" description="Run a scan to populate column metadata stats." />
      ) : (
        <EmptyState title="Loading" description="Fetching zero-scan quality profile…" />
      )
    },
    {
      key: "autocomp" as const,
      label: "AutoComp",
      content: autocomp.data ? (
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <p className="text-sm font-medium">Static partition order</p>
            <ul className="mt-2 list-disc pl-5 text-sm">
              {autocomp.data.static_order.map((p, index) => (
                <li key={`static-${index}-${p ?? "none"}`}><code>{p}</code></li>
              ))}
            </ul>
          </div>
          <div>
            <p className="text-sm font-medium">Benefit-score order</p>
            <ul className="mt-2 list-disc pl-5 text-sm">
              {autocomp.data.autocomp_order.map((p, index) => (
                <li key={`auto-${index}-${p ?? "none"}`}><code>{p}</code></li>
              ))}
            </ul>
          </div>
          <div className="md:col-span-2">
            <Badge variant={autocomp.data.autocomp_wins ? "success" : "secondary"}>
              {autocomp.data.autocomp_wins ? "AutoComp wins" : "Static wins"} · ratio {autocomp.data.improvement_ratio.toFixed(2)}×
            </Badge>
          </div>
        </div>
      ) : autocomp.isError ? (
        <EmptyState title="Not enough partitions" description="AutoComp compare needs partition stats." />
      ) : (
        <EmptyState title="Loading" description="Comparing scheduling strategies…" />
      )
    }
  ];

  const tabs = visibleTabs ? tabDefs.filter((item) => visibleTabs.includes(item.key)) : tabDefs;

  const content = (
    <div className={embedded ? "td-chart-card-content td-chart-card-content-embedded" : "td-chart-card-content"}>
      {lastSnapshotCommittedAt ? (
        <p className="mb-3 text-xs text-muted-foreground">
          Data freshness anchor: last snapshot commit {formatTime(lastSnapshotCommittedAt)}
        </p>
      ) : null}

      {hideTabsWhenSingle && tabs.length === 1 ? (
        tabs[0]?.content
      ) : (
        <Tabs defaultValue={tabs[0]?.key}>
          <TabsList>
            {tabs.map((tab) => (
              <TabsTrigger key={tab.key} value={tab.key}>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
          {tabs.map((tab) => (
            <TabsContent key={tab.key} value={tab.key}>
              {tab.content}
            </TabsContent>
          ))}
        </Tabs>
      )}
    </div>
  );

  if (embedded) return content;

  return (
    <Card className="td-chart-card">
      <div className="td-chart-card-header">
        <span className="td-chart-card-title">AI & observability</span>
        <div className="td-chart-card-actions">
          <Button size="sm" loading={explain.isPending || loading} onClick={() => explain.mutate()}>
            Explain health change
          </Button>
        </div>
      </div>
      <CardContent className="p-0">{content}</CardContent>
    </Card>
  );
};
