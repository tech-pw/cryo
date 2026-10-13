import type { ColumnDef } from "@tanstack/react-table";
import { AlertTriangle, ArrowRight, Check, FlaskConical, ShieldX } from "lucide-react";
import { useMemo, useState } from "react";
import type {
  CapabilityManifest,
  CapabilitySupportStatus,
  MaturityTier,
  OperationCapability
} from "../../api/types";
import { Badge, type BadgeProps } from "../../components/ui/badge";
import { DataTable } from "../../components/ui/data-table";
import { cn } from "../../lib/cn";
import { useConnectionsContext } from "./ConnectionsContext";

type BadgeVariant = NonNullable<BadgeProps["variant"]>;

const SUPPORT_LABELS: Record<CapabilitySupportStatus, string> = {
  supported: "Direct",
  conditional: "Conditional",
  fallback_required: "Fallback",
  configuration_only: "Config only",
  unsupported: "Blocked"
};

const SUPPORT_VARIANTS: Record<CapabilitySupportStatus, BadgeVariant> = {
  supported: "success",
  conditional: "warning",
  fallback_required: "default",
  configuration_only: "secondary",
  unsupported: "danger"
};

const MATURITY_VARIANTS: Record<MaturityTier, BadgeVariant> = {
  ga: "success",
  beta: "default",
  experimental: "warning",
  blocked: "danger"
};

const EXECUTION_LABELS: Record<OperationCapability["execution_mode"], string> = {
  trino_sql: "Trino SQL",
  spark_job: "Spark job",
  native_subprocess: "Native subprocess",
  sql_fallback: "SQL fallback",
  flink_configuration: "Flink config",
  none: "No execution"
};

export const capabilitySupportLabel = (status: CapabilitySupportStatus): string =>
  SUPPORT_LABELS[status];

export const capabilitySupportVariant = (status: CapabilitySupportStatus): BadgeVariant =>
  SUPPORT_VARIANTS[status];

const SupportMark = ({ status }: { status: CapabilitySupportStatus }) => {
  const Icon =
    status === "supported"
      ? Check
      : status === "unsupported"
        ? ShieldX
        : status === "configuration_only"
          ? FlaskConical
          : AlertTriangle;
  return (
    <Badge variant={capabilitySupportVariant(status)} className="gap-1">
      <Icon className="size-3" aria-hidden="true" />
      {capabilitySupportLabel(status)}
    </Badge>
  );
};

const CapabilityDetail = ({ row }: { row: OperationCapability }) => {
  const notes = [...row.runtime_conditions, ...row.limitations];
  return (
    <div className="min-w-64 space-y-1.5">
      {row.fallback_engines.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1 text-xs">
          <span className="text-muted-foreground">Fallback</span>
          <ArrowRight className="size-3 text-muted-foreground" aria-hidden="true" />
          {row.fallback_engines.map((engine) => (
            <Badge key={engine} variant="outline">
              {engine}
            </Badge>
          ))}
        </div>
      ) : null}
      {notes.length > 0 ? (
        <ul className="space-y-0.5 text-xs leading-5 text-muted-foreground">
          {notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : (
        <span className="text-xs text-muted-foreground">No runtime caveats.</span>
      )}
    </div>
  );
};

export const CapabilityLedger = ({ manifest }: { manifest: CapabilityManifest }) => {
  const [engineFilter, setEngineFilter] = useState("all");
  const [supportFilter, setSupportFilter] = useState("all");
  const operationByKey = useMemo(
    () => new Map(manifest.operations.map((operation) => [operation.operation, operation])),
    [manifest.operations]
  );
  const engineByKey = useMemo(
    () => new Map(manifest.engines.map((engine) => [engine.engine, engine])),
    [manifest.engines]
  );
  const rows = useMemo(
    () =>
      manifest.matrix.filter(
        (row) =>
          (engineFilter === "all" || row.engine === engineFilter) &&
          (supportFilter === "all" || row.support === supportFilter)
      ),
    [engineFilter, manifest.matrix, supportFilter]
  );

  const columns = useMemo<ColumnDef<OperationCapability, unknown>[]>(
    () => [
      {
        accessorKey: "engine",
        header: "Engine",
        cell: ({ row }) => {
          const engine = engineByKey.get(row.original.engine);
          return (
            <div className="space-y-1">
              <span className="font-medium">{engine?.label ?? row.original.engine}</span>
              <Badge variant={MATURITY_VARIANTS[engine?.maturity ?? row.original.maturity]}>
                {(engine?.maturity ?? row.original.maturity).toUpperCase()}
              </Badge>
            </div>
          );
        }
      },
      {
        accessorKey: "operation",
        header: "Operation",
        cell: ({ row }) => {
          const operation = operationByKey.get(row.original.operation);
          return (
            <div className="min-w-48">
              <p className="font-medium">{operation?.label ?? row.original.operation}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">{operation?.description}</p>
            </div>
          );
        }
      },
      {
        accessorKey: "support",
        header: "Support",
        cell: ({ row }) => (
          <div className="space-y-1">
            <SupportMark status={row.original.support} />
            <p className="text-xs text-muted-foreground">{row.original.maturity.toUpperCase()}</p>
          </div>
        )
      },
      {
        accessorKey: "execution_mode",
        header: "Execution",
        cell: ({ row }) => (
          <span className="text-xs font-medium">
            {EXECUTION_LABELS[row.original.execution_mode]}
          </span>
        )
      },
      {
        id: "conditions",
        header: "Conditions & limits",
        cell: ({ row }) => <CapabilityDetail row={row.original} />
      },
      {
        id: "evidence",
        header: "Evidence",
        cell: ({ row }) => (
          <details className="min-w-44 text-xs">
            <summary className="cursor-pointer font-medium text-foreground">
              {row.original.evidence.length} contracts
            </summary>
            <div className="mt-2 space-y-2 text-muted-foreground">
              {row.original.evidence.map((item) => (
                <div key={`${item.kind}-${item.path}-${item.symbol}`}>
                  <p className="uppercase tracking-wide">{item.kind}</p>
                  <code className="break-all text-[11px] text-foreground">
                    {item.path}::{item.symbol}
                  </code>
                </div>
              ))}
              <p>Owner: {row.original.owner}</p>
            </div>
          </details>
        )
      }
    ],
    [engineByKey, operationByKey]
  );

  const implicitGaps =
    manifest.engines.length * manifest.operations.length - manifest.matrix.length;
  const executingEngines = manifest.engines.filter((engine) => engine.execution_available).length;

  return (
    <div className="space-y-4">
      <div className="grid overflow-hidden rounded-[var(--radius-lg)] border border-border bg-[var(--color-surface-sunken)] sm:grid-cols-3">
        {[
          ["Contract", `v${manifest.schema_version}`],
          ["Declared cells", manifest.matrix.length.toString()],
          ["Implicit gaps", implicitGaps.toString()]
        ].map(([label, value], index) => (
          <div
            key={label}
            className={cn(
              "px-4 py-3",
              index > 0 && "border-t border-border sm:border-l sm:border-t-0"
            )}
          >
            <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              {label}
            </p>
            <p className="mt-1 text-xl font-semibold [font-variant-numeric:tabular-nums]">{value}</p>
          </div>
        ))}
      </div>

      <section className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-[var(--primitive-surface)] shadow-[var(--shadow-card)]">
        <div className="flex flex-wrap items-end justify-between gap-3 border-b border-border px-4 py-3">
          <div>
            <h3 className="text-sm font-semibold tracking-[-0.01em]">Maintenance execution matrix</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              {executingEngines} engines execute work; configuration-only paths are never counted
              as success.
            </p>
          </div>
        </div>
        <DataTable
          columns={columns}
          data={rows}
          density="compact"
          getRowId={(row) => `${row.engine}-${row.operation}`}
          emptyTitle="No capabilities match"
          emptyDescription="Change the engine or support filter."
          toolbar={
            <div className="flex flex-wrap gap-2">
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                Engine
                <select
                  aria-label="Filter capabilities by engine"
                  className="h-8 rounded-[var(--radius-sm)] border border-border bg-background px-2 text-xs text-foreground"
                  value={engineFilter}
                  onChange={(event) => setEngineFilter(event.target.value)}
                >
                  <option value="all">All engines</option>
                  {manifest.engines.map((engine) => (
                    <option key={engine.engine} value={engine.engine}>
                      {engine.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                Support
                <select
                  aria-label="Filter capabilities by support"
                  className="h-8 rounded-[var(--radius-sm)] border border-border bg-background px-2 text-xs text-foreground"
                  value={supportFilter}
                  onChange={(event) => setSupportFilter(event.target.value)}
                >
                  <option value="all">All states</option>
                  {Object.entries(SUPPORT_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          }
        />
      </section>

      <section
        aria-labelledby="catalog-capabilities-heading"
        className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-[var(--primitive-surface)] shadow-[var(--shadow-card)]"
      >
        <div className="border-b border-border px-4 py-3">
          <h3 id="catalog-capabilities-heading" className="text-sm font-semibold tracking-[-0.01em]">
            Catalog read adapters
          </h3>
          <p className="mt-1 text-sm text-muted-foreground">
            Catalog access discovers metadata. It never selects or executes a maintenance engine.
          </p>
        </div>
        <div className="divide-y divide-border">
          {manifest.catalogs.map((catalog) => (
            <div
              key={catalog.catalog_type}
              className="grid gap-2 px-3 py-3 md:grid-cols-[minmax(10rem,0.8fr)_minmax(12rem,1fr)_2fr]"
            >
              <div>
                <span className="font-medium">{catalog.label}</span>
                <Badge variant={MATURITY_VARIANTS[catalog.maturity]} className="ml-2">
                  {catalog.maturity.toUpperCase()}
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground">{catalog.protocol}</p>
              <div className="text-xs text-muted-foreground">
                <p>{catalog.capabilities.join(" / ")}</p>
                {catalog.limitations.map((limitation) => (
                  <p key={limitation} className="mt-1 text-[var(--color-status-warning)]">
                    {limitation}
                  </p>
                ))}
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
};

export const CapabilitiesTab = () => {
  const { capabilities } = useConnectionsContext();

  if (capabilities.isError) {
    return (
      <div
        role="alert"
        className="rounded-[var(--radius-lg)] border border-[var(--color-status-danger)] px-4 py-3 text-sm"
      >
        Capability contract unavailable: {(capabilities.error as Error).message}
      </div>
    );
  }

  if (!capabilities.data) {
    return (
      <div className="space-y-3" aria-label="Loading capability contract">
        <div className="h-20 animate-pulse rounded-[var(--radius-lg)] bg-[var(--color-surface-sunken)]" />
        <div className="h-72 animate-pulse rounded-[var(--radius-lg)] bg-[var(--color-surface-sunken)]" />
      </div>
    );
  }

  return <CapabilityLedger manifest={capabilities.data} />;
};
