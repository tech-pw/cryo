import { Copy } from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { narratePlan, previewMaintenance, verifyPlan } from "../api/client";
import type { JsonObject, MaintenancePlan, Mode, SavingsProjectionRead, TablePreviewRead, TableRead } from "../api/types";
import { CostPrecisionBadge } from "./CostPrecisionBadge";
import { StatStrip } from "./common";
import { SavingsByOperation } from "./SavingsByOperation";
import { useAuth } from "../lib/auth";
import { ENGINE_VIRTUAL_OPTIONS, isNativeStatement, parseEngineVirtual, type EngineVirtualValue } from "../lib/engines";
import { formatBytesGb, formatCurrency, formatGb, formatNumber } from "../lib/format";
import { shortRunId } from "../lib/operations";
import type { CostByOperation } from "../api/types";
import { Button } from "./ui/button";
import { Descriptions } from "./ui/descriptions";
import { EmptyState } from "./ui/empty-state";
import { Segmented } from "./ui/segmented";
import { Select } from "./ui/select";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "./ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";

interface Props {
  open: boolean;
  tables: TableRead[];
  onClose: () => void;
  params?: JsonObject;
  onRunTable?: (table: TableRead) => Promise<{ run_id: string }>;
  runDisabledReason?: string | null;
}

export const formatMeasuredTotal = (
  values: Array<number | null | undefined>,
  formatValue: (value: number) => string,
  emptyLabel: string
): string => {
  const measured = values.filter((value): value is number => value != null && Number.isFinite(value));
  if (measured.length === 0) return emptyLabel;
  const formatted = formatValue(measured.reduce((sum, value) => sum + value, 0));
  if (measured.length < values.length) return `${formatted} (${measured.length} of ${values.length} measured)`;
  return formatted;
};

const costText = (value: number | null | undefined): string =>
  value == null || !Number.isFinite(value) ? "Cost data unavailable" : formatCurrency(value);

const bytesText = (value: number | null | undefined): string =>
  value == null || !Number.isFinite(value) ? "Not measured" : formatBytesGb(value);

const savingsToByOperation = (savings?: SavingsProjectionRead | null): CostByOperation[] => {
  if (!savings?.by_operation) return [];
  return Object.entries(savings.by_operation)
    .filter(([, amount]) => amount > 0)
    .map(([operation, savings_usd_month]) => ({
      operation: operation as CostByOperation["operation"],
      savings_usd_month,
      reclaimable_bytes: null
    }));
};

const buildDeterministicNarrative = (
  tableRef: string | undefined,
  operations: string[],
  notes: string[],
  verifierSummary?: string
): string => {
  const opOrder = operations.length ? operations.join(" -> ") : "no maintenance operations";
  const noteSummary = notes.length ? notes.slice(0, 3).join(" | ") : "no planner notes";
  const verifier = verifierSummary ?? "Verifier status is unavailable.";
  return `${tableRef ?? "Selected table"} executes ${opOrder}. ${noteSummary}. ${verifier}`;
};

export const PlanPreview = ({ open, tables, onClose, params = {}, onRunTable, runDisabledReason = null }: Props) => {
  const { api } = useAuth();
  const [engineVirtual, setEngineVirtual] = useState<EngineVirtualValue>("trino");
  const [mode, setMode] = useState<Mode>("deep");
  const [activeTableKey, setActiveTableKey] = useState<string>();
  const [activeStatementKey, setActiveStatementKey] = useState<string>();

  const requestTables = useMemo(
    () =>
      tables.map((table) => ({
        schema_name: table.schema_name,
        table_name: table.table_name,
        stats: { ...(table.stats ?? {}), ...(table.stats?.raw ?? {}) },
        partitions: Array.isArray(table.stats?.raw?.partitions)
          ? (table.stats?.raw?.partitions as JsonObject[])
          : []
      })),
    [tables]
  );

  const tableByRef = useMemo(
    () => new Map(tables.map((table) => [`${table.schema_name}.${table.table_name}`, table] as const)),
    [tables]
  );

  const preview = useMutation({
    mutationFn: () =>
      previewMaintenance(api, {
        engine: parseEngineVirtual(engineVirtual).engine,
        mode,
        params,
        tables: requestTables
      })
  });

  const planVerifier = useMutation({
    mutationFn: (item: TablePreviewRead) =>
      verifyPlan(api, {
        plan: {
          table_ref: item.table_ref,
          mode,
          operations: item.statements.map((statement) => ({
            type: statement.action as MaintenancePlan["operations"][number]["type"],
            params: (statement.payload ?? {}) as Record<string, unknown>,
            predicates: null
          })),
          notes: item.notes
        }
      })
  });
  const narration = useMutation({
    mutationFn: (item: TablePreviewRead) =>
      narratePlan(api, {
        table_ref: item.table_ref ?? "table",
        notes: item.notes,
        operations: item.statements.map((statement) => statement.action),
        sql_statements: item.statements.map((statement) => statement.sql)
      })
  });
  const runTable = useMutation({
    mutationFn: (table: TableRead) => onRunTable?.(table) ?? Promise.reject(new Error("Run is not available here."))
  });

  const estimate = useMemo(() => {
    const statements = preview.data?.items.flatMap((item) => item.statements) ?? [];
    const savingsItems = preview.data?.items.map((item) => item.savings).filter(Boolean) ?? [];
    const savingsValues = preview.data
      ? preview.data.items.map((item) => item.savings?.projected_savings_usd_month)
      : [];
    const reclaimValues = preview.data
      ? preview.data.items.map((item) => item.savings?.reclaimable_bytes)
      : [];
    const noneScanned = tables.every((table) => !table.stats);
    const measuredSavings = savingsValues.filter((value): value is number => value != null && Number.isFinite(value));
    return {
      tables: tables.length,
      optimizeChunks: statements.filter((statement) => statement.action === "optimize").length,
      statements: statements.length,
      partitionsLabel: formatMeasuredTotal(
        tables.map((table) => table.stats?.total_partitions),
        (value) => formatNumber(value, true),
        noneScanned ? "Not scanned" : "Not measured"
      ),
      sizeLabel: formatMeasuredTotal(
        tables.map((table) => table.stats?.total_size_gb),
        formatGb,
        noneScanned ? "Not scanned" : "Not measured"
      ),
      savingsLabel: formatMeasuredTotal(savingsValues, formatCurrency, "Cost data unavailable"),
      reclaimableLabel: formatMeasuredTotal(reclaimValues, formatBytesGb, "Not measured"),
      hasMeasuredSavings: measuredSavings.some((value) => value > 0),
      precision: savingsItems[0]?.precision
    };
  }, [preview.data, tables]);

  useEffect(() => {
    if (open && requestTables.length) preview.mutate();
  }, [open, engineVirtual, mode, requestTables.length]);

  useEffect(() => {
    if (!open || !preview.data?.items.length) return;
    planVerifier.mutate(preview.data.items[0]);
    setActiveTableKey(preview.data.items[0].table_ref ?? "table-0");
  }, [open, preview.data?.items, mode]);

  useEffect(() => {
    if (!open || !preview.data?.items.length) return;
    narration.mutate(preview.data.items[0]);
  }, [open, preview.data?.items, mode]);

  const queueRun = (tableRef: string) => {
    const table = tableByRef.get(tableRef as `${string}.${string}`);
    if (!table || !onRunTable) return;
    runTable.mutate(table, {
      onSuccess: (result) => {
        toast.success(`Run queued for ${tableRef}`, {
          description: (
            <Link to={`/operations/runs/${result.run_id}`} className="underline">
              View run {shortRunId(result.run_id)}
            </Link>
          )
        });
      },
      onError: (error) => toast.error((error as Error).message)
    });
  };

  return (
    <Sheet open={open} onOpenChange={(next) => !next && onClose()}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-[820px]">
        <SheetHeader>
          <SheetTitle>Plan preview</SheetTitle>
        </SheetHeader>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Segmented
            value={engineVirtual}
            onChange={setEngineVirtual}
            options={[...ENGINE_VIRTUAL_OPTIONS]}
          />
          <Select
            value={mode}
            onChange={(value) => value && setMode(value as Mode)}
            options={[
              { label: "Light", value: "light" },
              { label: "Standard", value: "standard" },
              { label: "Deep", value: "deep" },
              { label: "Manifest only", value: "manifest_only" }
            ]}
            style={{ width: 140 }}
          />
        </div>

        {!requestTables.length ? (
          <EmptyState title="No tables selected" description="Select at least one table to preview." />
        ) : (
          <div className="mt-4 flex w-full flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <CostPrecisionBadge precision={estimate.precision} />
              {estimate.hasMeasuredSavings ? (
                <p className="text-sm text-muted-foreground">
                  Planned savings use persisted scan data for the selected operations only.
                </p>
              ) : null}
            </div>
            <StatStrip
              items={[
                { label: "Tables", value: formatNumber(estimate.tables) },
                { label: "Datalake size", value: estimate.sizeLabel },
                { label: "Partitions", value: estimate.partitionsLabel },
                { label: "Optimize chunks", value: formatNumber(estimate.optimizeChunks) },
                { label: "Statements", value: formatNumber(estimate.statements) },
                { label: "Planned savings", value: estimate.savingsLabel },
                { label: "Reclaimable", value: estimate.reclaimableLabel }
              ]}
            />
            {planVerifier.data ? (
              <Descriptions
                bordered
                size="small"
                column={2}
                items={[
                  { label: "Plan verifier", children: planVerifier.data.verified ? "Verified" : "Violations detected" },
                  { label: "Version", children: planVerifier.data.verifier_version },
                  { label: "Invariants checked", children: planVerifier.data.invariants_checked.length },
                  { label: "Violations", children: planVerifier.data.violations.length }
                ]}
              />
            ) : null}
            {planVerifier.data?.violations.length ? (
              <p className="text-sm text-[var(--color-status-warning)]">
                {planVerifier.data.violations
                  .map((violation) => `${violation.severity.toUpperCase()}: ${violation.message}`)
                  .join(" · ")}
              </p>
            ) : null}
            {preview.data?.items[0] ? (
              <Descriptions
                bordered
                size="small"
                column={1}
                items={[
                  {
                    label: "Explain this plan",
                    children:
                      narration.data?.deterministic_summary ??
                      buildDeterministicNarrative(
                        preview.data.items[0].table_ref,
                        preview.data.items[0].statements.map((statement) => statement.action),
                        preview.data.items[0].notes,
                        planVerifier.data
                          ? planVerifier.data.verified
                            ? "Plan verifier passed all configured invariants."
                            : `Plan verifier found ${planVerifier.data.violations.length} violation(s).`
                          : undefined
                      )
                  },
                  {
                    label: "SQL narration",
                    children:
                      narration.data?.llm_narration ??
                      "LLM narration unavailable; deterministic explanation remains authoritative."
                  }
                ]}
              />
            ) : null}
            <Tabs value={activeTableKey} onValueChange={setActiveTableKey}>
              <TabsList className="flex h-auto flex-wrap justify-start">
                {(preview.data?.items ?? []).map((item, tableIndex) => (
                  <TabsTrigger key={item.table_ref ?? `table-${tableIndex}`} value={item.table_ref ?? `table-${tableIndex}`}>
                    {item.table_ref}
                  </TabsTrigger>
                ))}
              </TabsList>
              {(preview.data?.items ?? []).map((item, tableIndex) => {
                const tableKey = item.table_ref ?? `table-${tableIndex}`;
                return (
                  <TabsContent key={tableKey} value={tableKey} className="space-y-3">
                    {onRunTable || runDisabledReason ? (
                      <Button
                        variant="primary"
                        size="sm"
                        loading={runTable.isPending}
                        disabled={!onRunTable || Boolean(runDisabledReason)}
                        title={runDisabledReason ?? undefined}
                        onClick={() => item.table_ref && queueRun(item.table_ref)}
                      >
                        Run this plan
                      </Button>
                    ) : null}
                    {item.savings ? (
                      <Descriptions
                        bordered
                        size="small"
                        column={2}
                        items={[
                          { label: "Now", children: costText(item.savings.current_monthly_cost_usd) },
                          {
                            label: "After maintenance",
                            children: costText(item.savings.projected_monthly_cost_usd)
                          },
                          {
                            label: "Savings / mo",
                            children: costText(item.savings.projected_savings_usd_month)
                          },
                          { label: "Reclaimable", children: bytesText(item.savings.reclaimable_bytes) },
                          ...(item.savings.query_request_savings_usd_month
                            ? [
                                {
                                  label: "Query request savings",
                                  span: 2 as const,
                                  children: `${formatCurrency(item.savings.query_request_savings_usd_month)}/mo (estimate, not storage)`
                                }
                              ]
                            : [])
                        ]}
                      />
                    ) : null}
                    {item.savings ? <SavingsByOperation items={savingsToByOperation(item.savings)} /> : null}
                    {item.notes.length ? (
                      <p className="text-sm text-muted-foreground">{item.notes.join(" · ")}</p>
                    ) : null}
                    <Tabs
                      value={activeStatementKey ?? `${item.statements[0]?.action}-0`}
                      onValueChange={setActiveStatementKey}
                      orientation="vertical"
                      className="flex gap-4"
                    >
                      <TabsList className="flex h-auto min-w-[9rem] flex-col items-stretch">
                        {item.statements.map((statement, index) => (
                          <TabsTrigger
                            key={`${statement.action}-${index}`}
                            value={`${statement.action}-${index}`}
                            className="justify-start"
                          >
                            {statement.action}
                          </TabsTrigger>
                        ))}
                      </TabsList>
                      {item.statements.map((statement, index) => (
                        <TabsContent key={`${statement.action}-${index}`} value={`${statement.action}-${index}`}>
                          <div className="sql-block">
                            <Button
                              size="sm"
                              onClick={() => {
                                const copyText =
                                  isNativeStatement(statement.sql) && statement.payload
                                    ? JSON.stringify(statement.payload, null, 2)
                                    : statement.sql;
                                void navigator.clipboard.writeText(copyText);
                                toast.success("Copied to clipboard");
                              }}
                            >
                              <Copy className="size-3.5" />
                              Copy
                            </Button>
                            {isNativeStatement(statement.sql) || statement.engine === "native" ? (
                              <>
                                <code className="mono-input mt-2 block text-sm text-muted-foreground">
                                  {statement.sql}
                                </code>
                                <pre>{JSON.stringify(statement.payload ?? {}, null, 2)}</pre>
                              </>
                            ) : (
                              <pre>{statement.sql}</pre>
                            )}
                          </div>
                        </TabsContent>
                      ))}
                    </Tabs>
                  </TabsContent>
                );
              })}
            </Tabs>
          </div>
        )}
        {preview.isPending ? <p className="mt-3 text-sm text-muted-foreground">Rendering plan...</p> : null}
        {preview.isError ? (
          <p className="mt-3 text-sm text-[var(--color-status-danger)]">
            Preview failed: {(preview.error as Error).message}
          </p>
        ) : null}
      </SheetContent>
    </Sheet>
  );
};
