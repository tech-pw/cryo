import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { getTable, getTableAudit, getTableCost } from "../../api/client";
import { MaintenanceActions } from "../../components/table-detail/operations/MaintenanceActions";
import { readDeductionList } from "../../components/table-detail/operations/operationCatalog";
import {
  getTableMaintenanceStatus,
  type BlockingReasonRead,
} from "../../api/maintenanceStatus";
import { CompactionAnalysis } from "../../components/table-detail/maintenance/CompactionAnalysis";
import { EffectiveSettings } from "../../components/table-detail/maintenance/EffectiveSettings";
import { TableRunGrid } from "../../components/table-detail/runs/TableRunGrid";
import { QueryErrorAlert } from "../../components/common";
import { Badge } from "../../components/ui/badge";
import { useAuth } from "../../lib/auth";
import { auditBytesLabel } from "../../lib/auditFeed";
import { formatTime, humanizeInstants } from "../../lib/format";
import { tableKeys } from "./tableKeys";
import "./maintenanceTab.css";

const ISO_INSTANT = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/;

const BYTE_KEYS = new Set([
  "actual_bytes_freed",
  "bytes",
  "bytes_freed",
  "bytes_rewritten",
  "size_bytes"
]);

export const formatMaintenanceValue = (key: string, value: unknown): string => {
  if (value === null || value === undefined || value === "") return "Unknown";
  if (BYTE_KEYS.has(key)) return typeof value === "number" ? auditBytesLabel(value) : "Unknown";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "Unknown";
  if (typeof value === "string") return ISO_INSTANT.test(value) ? formatTime(value) : value;
  if (Array.isArray(value)) {
    if (value.length === 0) return "(empty)";
    return value.map((item) => (item == null || item === "" ? "Unknown" : String(item))).join(", ");
  }
  return JSON.stringify(value);
};

const eligibilityLabel = (eligible: boolean | null | undefined): string => {
  if (eligible === true) return "Eligible for nightly selection";
  if (eligible === false) return "Not eligible for nightly selection";
  return "Eligibility unknown";
};

const ReasonValues = ({ reason }: { reason: BlockingReasonRead }) => {
  const entries = Object.entries(reason.values ?? {});
  if (entries.length === 0) return <p className="text-xs text-muted-foreground">No values recorded.</p>;
  return (
    <dl className="mt-2 grid gap-x-4 gap-y-1 sm:grid-cols-2">
      {entries.map(([key, value]) => (
        <div key={key} className="min-w-0">
          <dt className="text-xs text-muted-foreground">{key}</dt>
          <dd className="truncate text-sm text-foreground [font-variant-numeric:tabular-nums]">
            {formatMaintenanceValue(key, value)}
          </dd>
        </div>
      ))}
    </dl>
  );
};

const EligibilityChecklist = ({ reasons }: { reasons: BlockingReasonRead[] }) => {
  if (reasons.length === 0) {
    return <p className="text-sm text-muted-foreground">No blocking reasons.</p>;
  }
  return (
    <ul aria-label="Eligibility checklist" className="space-y-3">
      {reasons.map((reason, index) => (
        <li key={`${reason.code}-${index}`} className="rounded-[var(--radius-default)] border border-border p-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <p className="text-sm font-medium text-foreground">{humanizeInstants(reason.summary)}</p>
            <Badge variant={reason.blocks_nightly_selection ? "warning" : "secondary"}>
              {reason.blocks_nightly_selection ? "Blocks nightly selection" : "Does not block nightly selection"}
            </Badge>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {reason.code} · {reason.predicate}
            {reason.until ? ` · until ${formatTime(reason.until)}` : ""}
          </p>
          <ReasonValues reason={reason} />
        </li>
      ))}
    </ul>
  );
};

const AUDIT_WINDOW = 40;

const TableMaintenanceActions = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const enabled = tableId.length > 0;
  const table = useQuery({ queryKey: tableKeys.detail(tableId, token), queryFn: () => getTable(api, tableId), enabled });
  const audit = useQuery({
    queryKey: tableKeys.audit(tableId, token, AUDIT_WINDOW),
    queryFn: () => getTableAudit(api, tableId, { limit: AUDIT_WINDOW }),
    enabled
  });
  const cost = useQuery({ queryKey: tableKeys.cost(tableId, token), queryFn: () => getTableCost(api, tableId), enabled, retry: false });
  if (table.isError) return <QueryErrorAlert error={table.error} title="Couldn't load maintenance actions" />;
  if (table.isLoading) return <p className="td-mt-lede">Loading maintenance actions…</p>;
  if (!table.data?.stats) return <p className="td-mt-lede">Scan this table to evaluate maintenance actions.</p>;
  return (
    <div className="mb-6">
      <MaintenanceActions
        tableId={tableId}
        stats={table.data.stats}
        audit={audit.data ?? []}
        orphanBytes={cost.data?.orphan_bytes}
        deductions={readDeductionList(table.data.stats.raw)}
        id="run-on-demand"
      />
    </div>
  );
};

export const MaintenanceTab = () => {
  const { tableId = "" } = useParams();
  const { api, token } = useAuth();
  const status = useQuery({
    queryKey: tableKeys.maintenanceStatus(tableId, token),
    queryFn: () => getTableMaintenanceStatus(api, tableId),
    enabled: tableId.length > 0
  });
  const data = status.data;
  const scanBackoff = (data?.blocking_reasons ?? []).find((reason) => reason.code === "scan_retry_backoff");
  const blocking = (data?.blocking_reasons ?? []).filter((reason) => reason.blocks_nightly_selection);

  return (
    <div className="td-maintenance-workspace">
      <div className="td-mt-grid">
        <section className="td-mt-card" aria-labelledby="maintenance-tonight">
          <p className="td-mt-kicker">1 · Readiness</p>
          {!data ? <h3 id="maintenance-tonight" className="td-mt-status">Nightly selection</h3> : null}
          <QueryErrorAlert error={status.error} title="Couldn't load maintenance status" />
          {!data && status.isLoading ? <p className="text-sm text-muted-foreground">Loading maintenance status</p> : null}
          {!data && status.isSuccess ? <p className="td-mt-lede">No maintenance evaluation recorded yet.</p> : null}
          {data ? (
            <>
              <h3 id="maintenance-tonight" className="td-mt-status" data-eligible={data.eligible === true || undefined}>
                {eligibilityLabel(data.eligible)}
              </h3>
              <p className="td-mt-lede">
                {blocking.length
                  ? `${blocking.length} gate${blocking.length === 1 ? "" : "s"} must clear first.`
                  : data.eligible
                    ? "No gate blocks it. Engine capacity and the run's budget decide whether it's picked."
                    : "No blocking reason was returned."}
              </p>
              <dl className="td-mt-facts">
                <div>
                  <dt>Evaluated</dt>
                  <dd>{data.evaluated_at ? formatTime(data.evaluated_at) : "Unknown"}</dd>
                </div>
                <div>
                  <dt>Cooldown</dt>
                  <dd>{formatMaintenanceValue("cooldown_hours", data.thresholds?.cooldown_hours)} h</dd>
                </div>
                <div>
                  <dt>Stats window</dt>
                  <dd>{formatMaintenanceValue("max_stats_age_days", data.thresholds?.max_stats_age_days)} days</dd>
                </div>
                <div>
                  <dt>Next nightly</dt>
                  <dd>{data.next_nightly_at ? formatTime(data.next_nightly_at) : "Next nightly not recorded"}</dd>
                </div>
              </dl>
              {scanBackoff ? (
                <p className="td-mt-lede">Scan backoff until {scanBackoff.until ? formatTime(scanBackoff.until) : "not recorded"}.</p>
              ) : null}
              <section className="mt-4" aria-labelledby="maintenance-checklist">
                <h4 id="maintenance-checklist" className="td-mt-subhead">
                  Eligibility checklist
                </h4>
                <EligibilityChecklist reasons={data.blocking_reasons ?? []} />
              </section>
              <details className="td-mt-details">
                <summary>How nightly selection works</summary>
                <p>{data.eligibility_basis}</p>
                {(data.omitted ?? []).length > 0 ? (
                  <ul>
                    {data.omitted.map((gate) => (
                      <li key={gate.code}>
                        <span className="font-medium text-foreground">{gate.code}</span>
                        {" is not evaluated here: "}
                        {gate.why}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </details>
            </>
          ) : null}
        </section>
        <EffectiveSettings tableId={tableId} />
      </div>
      <div className="td-mt-stage">
        <p className="td-mt-kicker">2 · Choose an action</p>
        <p className="td-mt-stage-description">Review a plan before running maintenance. Nightly eligibility and on-demand execution have separate checks.</p>
        <TableMaintenanceActions tableId={tableId} />
      </div>
      <div className="td-mt-stage">
        <p className="td-mt-kicker">3 · Follow the outcome</p>
        <TableRunGrid tableId={tableId} />
      </div>
      <CompactionAnalysis tableId={tableId} />
    </div>
  );
};
