import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, CheckCircle2, Circle, Loader2, MinusCircle, Play, RefreshCw, XCircle } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getTableAudit, getTableRuns } from "../../../api/client";
import { useRunLiveEvents } from "../../../hooks/useRunLiveEvents";
import { useAuth } from "../../../lib/auth";
import { formatTime } from "../../../lib/format";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import { QueryErrorAlert } from "../../common";
import { Button } from "../../ui/button";
import { Skeleton } from "../../ui/skeleton";
import {
  OPERATION_SHORT,
  buildRunColumns,
  formatMs,
  gridRows,
  isActiveTone,
  statChanges,
  toneFor,
  type CellTone,
  type OpCell,
  type RunColumn
} from "./runModel";
import "./runs.css";

const RUNS_LIMIT = 25;
const AUDIT_LIMIT = 200;
const LIVE_LOG_WINDOW_MS = 24 * 60 * 60 * 1000;

type Filter = "all" | "manual" | "nightly";

const TONE_LABEL: Record<CellTone, string> = {
  success: "Succeeded",
  failed: "Failed",
  running: "Running",
  queued: "Waiting",
  skipped: "Skipped",
  none: "Not in run"
};

const ToneIcon = ({ tone }: { tone: CellTone }) => {
  if (tone === "success") return <CheckCircle2 size={14} aria-hidden="true" />;
  if (tone === "failed") return <XCircle size={14} aria-hidden="true" />;
  if (tone === "running") return <Loader2 size={14} className="animate-spin" aria-hidden="true" />;
  if (tone === "skipped") return <MinusCircle size={14} aria-hidden="true" />;
  return <Circle size={14} aria-hidden="true" />;
};

const shortDate = (iso: string | null) =>
  iso ? new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(Date.parse(iso)) : "—";

export const TableRunGrid = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [filter, setFilter] = useState<Filter>("all");
  const runs = useQuery({
    queryKey: tableKeys.runs(tableId, token, RUNS_LIMIT, 0),
    queryFn: () => getTableRuns(api, tableId, { limit: RUNS_LIMIT, offset: 0 }),
    enabled: tableId.length > 0,
    refetchInterval: (query) =>
      (query.state.data ?? []).some((run) => isActiveTone(toneFor(run.status))) ? 5000 : false
  });
  const audit = useQuery({
    queryKey: tableKeys.audit(tableId, token, AUDIT_LIMIT),
    queryFn: () => getTableAudit(api, tableId, { limit: AUDIT_LIMIT }),
    enabled: tableId.length > 0,
    refetchInterval: (runs.data ?? []).some((run) => isActiveTone(toneFor(run.status))) ? 5000 : false
  });

  const allColumns = useMemo(() => buildRunColumns(runs.data ?? [], audit.data ?? []), [runs.data, audit.data]);
  const columns = allColumns.filter((column) => filter === "all" || (filter === "manual" ? column.manual : !column.manual));
  const rows = gridRows(columns);
  const peak = Math.max(1, ...columns.map((column) => column.durationMs ?? 0));
  const requestedRun = searchParams.get("run");
  const selected =
    columns.find((column) => column.run.run_id === requestedRun || column.key === requestedRun) ?? columns[columns.length - 1] ?? null;
  const select = (column: RunColumn) => {
    const next = new URLSearchParams(searchParams);
    next.set("run", column.run.run_id);
    setSearchParams(next, { replace: true });
  };
  const counts = {
    success: allColumns.filter((column) => column.tone === "success").length,
    failed: allColumns.filter((column) => column.tone === "failed").length,
    active: allColumns.filter((column) => isActiveTone(column.tone)).length
  };

  return (
    <section className="td-runs" id="maintenance-runs" aria-labelledby="maintenance-runs-title">
      <header className="td-runs-head">
        <div>
          <h2 id="maintenance-runs-title">Runs</h2>
          <p>
            Last {allColumns.length} run{allColumns.length === 1 ? "" : "s"} on this table ·{" "}
            <span className="td-runs-count" data-tone="success">{counts.success} succeeded</span>
            {counts.failed ? <> · <span className="td-runs-count" data-tone="failed">{counts.failed} failed</span></> : null}
            {counts.active ? <> · <span className="td-runs-count" data-tone="running">{counts.active} in progress</span></> : null}
          </p>
        </div>
        <div className="td-runs-tools">
          <div className="td-runs-filter" role="radiogroup" aria-label="Show runs">
            {(["all", "manual", "nightly"] as Filter[]).map((value) => (
              <button key={value} type="button" role="radio" aria-checked={filter === value} onClick={() => setFilter(value)}>
                {value === "all" ? "All" : value === "manual" ? "Manual" : "Nightly"}
              </button>
            ))}
          </div>
          <Button type="button" size="icon" variant="ghost" aria-label="Refresh runs" onClick={() => { void runs.refetch(); void audit.refetch(); }}>
            <RefreshCw aria-hidden="true" className={runs.isFetching ? "animate-spin" : undefined} />
          </Button>
        </div>
      </header>

      <QueryErrorAlert error={runs.error} title="Couldn't load runs" />
      {runs.isPending ? (
        <Skeleton className="h-56 w-full" />
      ) : columns.length === 0 ? (
        <div className="td-runs-empty">
          <Play size={18} aria-hidden="true" />
          <p>{allColumns.length ? "No runs match this filter." : "No maintenance has run on this table yet. Add operations above and run them."}</p>
        </div>
      ) : (
        <>
          <div className="td-grid-scroll">
            <div className="td-grid" style={{ gridTemplateColumns: `minmax(112px, max-content) repeat(${columns.length}, 30px)` }}>
              <div className="td-grid-label td-grid-label--bars">Duration</div>
              {columns.map((column) => (
                <button
                  key={`bar-${column.key}`}
                  type="button"
                  className="td-grid-bar"
                  data-tone={column.tone}
                  data-selected={selected?.key === column.key || undefined}
                  aria-label={`Run ${shortDate(column.startedAt)}, ${TONE_LABEL[column.tone]}, ${formatMs(column.durationMs)}${column.manual ? ", manual" : ", nightly"}`}
                  title={`${formatTime(column.startedAt)} · ${TONE_LABEL[column.tone]} · ${formatMs(column.durationMs)} · ${column.manual ? "manual" : "nightly"}`}
                  onClick={() => select(column)}
                >
                  <span style={{ height: `${Math.max(6, ((column.durationMs ?? 0) / peak) * 100)}%` }} />
                  {column.manual ? <i className="td-grid-manual" aria-hidden="true" /> : null}
                </button>
              ))}
              {rows.map((op) => (
                <GridRow key={op} op={op} columns={columns} selectedKey={selected?.key ?? null} onSelect={select} />
              ))}
              <div className="td-grid-label" />
              {columns.map((column, index) => (
                <div key={`date-${column.key}`} className="td-grid-date">
                  {index === 0 || index === columns.length - 1 || index % 6 === 0 ? shortDate(column.startedAt) : ""}
                </div>
              ))}
            </div>
          </div>
          <ul className="td-grid-legend" aria-label="Legend">
            {(["success", "failed", "running", "queued", "skipped", "none"] as CellTone[]).map((tone) => (
              <li key={tone} data-tone={tone}>
                <i aria-hidden="true" />
                {TONE_LABEL[tone]}
              </li>
            ))}
            <li className="td-grid-legend-manual">
              <i aria-hidden="true" />
              Manual run
            </li>
          </ul>
          {selected ? <RunDetailPanel column={selected} /> : null}
        </>
      )}
    </section>
  );
};

const GridRow = ({
  op,
  columns,
  selectedKey,
  onSelect
}: {
  op: string;
  columns: RunColumn[];
  selectedKey: string | null;
  onSelect: (column: RunColumn) => void;
}) => (
  <>
    <div className="td-grid-label">{OPERATION_SHORT[op] ?? op.replaceAll("_", " ")}</div>
    {columns.map((column) => {
      const cell = column.cells.get(op);
      const tone: CellTone = cell ? cell.tone : "none";
      return (
        <button
          key={`${op}-${column.key}`}
          type="button"
          className="td-grid-cell"
          data-tone={tone}
          data-selected={selectedKey === column.key || undefined}
          aria-label={`${OPERATION_SHORT[op] ?? op} on ${shortDate(column.startedAt)}: ${TONE_LABEL[tone]}`}
          title={`${OPERATION_SHORT[op] ?? op} · ${TONE_LABEL[tone]}${cell?.durationMs != null ? ` · ${formatMs(cell.durationMs)}` : ""}`}
          onClick={() => onSelect(column)}
        />
      );
    })}
  </>
);

const RunDetailPanel = ({ column }: { column: RunColumn }) => {
  const [tab, setTab] = useState<"steps" | "live">("steps");
  const run = column.run;
  const changes = statChanges(run);
  const cells = [...column.cells.values()].sort(
    (a, b) => Date.parse(a.startedAt ?? a.finishedAt ?? "") - Date.parse(b.startedAt ?? b.finishedAt ?? "")
  );
  const runStart = Date.parse(run.started_at ?? "");
  const runEnd = Date.parse(run.finished_at ?? "") || Date.now();
  const recent = Number.isFinite(runStart) && Date.now() - runStart < LIVE_LOG_WINDOW_MS;
  const engine = cells.find((cell) => cell.engine)?.engine ?? null;

  return (
    <div className="td-run-detail" data-tone={column.tone}>
      <div className="td-run-summary">
        <p className="td-run-status" data-tone={column.tone}>
          <ToneIcon tone={column.tone} /> {TONE_LABEL[column.tone]}
        </p>
        <dl>
          <div>
            <dt>Started</dt>
            <dd>{formatTime(run.started_at)}</dd>
          </div>
          <div>
            <dt>Duration</dt>
            <dd>
              {column.durationMs != null
                ? formatMs(column.durationMs)
                : Number.isFinite(runStart) && isActiveTone(column.tone)
                  ? `${formatMs(Date.now() - runStart)} so far`
                  : "—"}
            </dd>
          </div>
          <div>
            <dt>Trigger</dt>
            <dd>{column.manual ? `Manual · ${run.trigger ?? "api"}` : "Nightly schedule"}</dd>
          </div>
          <div>
            <dt>Mode · engine</dt>
            <dd>
              {run.mode}
              {engine ? ` · ${engine}` : ""}
            </dd>
          </div>
        </dl>
        {run.error ? (
          <div className="td-run-error" role="alert">
            <XCircle size={14} aria-hidden="true" />
            <pre>{run.error}</pre>
          </div>
        ) : null}
        {changes.length ? (
          <div className="td-run-changes">
            <p className="td-run-subhead">Before → next scan after</p>
            <table>
              <tbody>
                {changes.map((change) => (
                  <ChangeRow key={change.label} change={change} />
                ))}
              </tbody>
            </table>
            <p className="td-run-footnote">The after-scan can include new writes, so treat it as the table's state, not this run's effect alone.</p>
          </div>
        ) : null}
        <Link className="td-run-link" to={`/runs/${run.run_id}`}>
          Open full run <ArrowUpRight size={14} aria-hidden="true" />
        </Link>
      </div>

      <div className="td-run-logs">
        <div className="td-run-tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === "steps"} onClick={() => setTab("steps")}>
            Operations & statements
          </button>
          <button type="button" role="tab" aria-selected={tab === "live"} onClick={() => setTab("live")}>
            Live log
          </button>
        </div>
        {tab === "steps" ? (
          cells.length ? (
            <ol className="td-run-steps">
              {cells.map((cell) => (
                <StepRow key={cell.op} cell={cell} runStart={runStart} runEnd={runEnd} />
              ))}
            </ol>
          ) : (
            <p className="td-run-note">No per-operation record for this run{isActiveTone(column.tone) ? " yet; it appears as operations finish" : ""}.</p>
          )
        ) : recent ? (
          <LiveLog runId={run.run_id} jobId={run.job_id} />
        ) : (
          <p className="td-run-note">
            Live log lines are kept for 24 hours. For older runs, the recorded statements under “Operations & statements” are the log.
          </p>
        )}
      </div>
    </div>
  );
};

const ChangeRow = ({ change }: { change: ReturnType<typeof statChanges>[number] }) => {
  const { before, after } = change;
  const fmt = (value: number | null) =>
    value == null ? "—" : change.unit === "GB" ? `${value.toFixed(1)} GB` : value.toLocaleString("en-US");
  const delta = before != null && after != null ? after - before : null;
  const better = delta == null || delta === 0 ? null : change.lowerIsBetter ? delta < 0 : delta > 0;
  return (
    <tr>
      <th scope="row">{change.label}</th>
      <td>{fmt(before)}</td>
      <td aria-hidden="true">→</td>
      <td>
        <b>{fmt(after)}</b>
      </td>
      <td className="td-run-delta" data-better={better == null ? undefined : String(better)}>
        {delta == null || delta === 0
          ? "no change"
          : `${delta > 0 ? "+" : "−"}${change.unit === "GB" ? `${Math.abs(delta).toFixed(1)} GB` : Math.abs(delta).toLocaleString("en-US")}`}
      </td>
    </tr>
  );
};

const StepRow = ({ cell, runStart, runEnd }: { cell: OpCell; runStart: number; runEnd: number }) => {
  const start = Date.parse(cell.startedAt ?? "");
  const end = Date.parse(cell.finishedAt ?? "") || (Number.isFinite(start) ? Date.now() : Number.NaN);
  const span = runEnd - runStart;
  const placed = Number.isFinite(start) && Number.isFinite(end) && Number.isFinite(runStart) && span > 0;
  return (
    <li data-tone={cell.tone}>
      <div className="td-run-step-head">
        <span className="td-run-step-icon" data-tone={cell.tone}>
          <ToneIcon tone={cell.tone} />
        </span>
        <b>{OPERATION_SHORT[cell.op] ?? cell.op}</b>
        <span className="td-run-step-meta">
          {TONE_LABEL[cell.tone].toLowerCase()} · {formatMs(cell.durationMs)}
        </span>
      </div>
      {placed ? (
        <div className="td-run-gantt" aria-hidden="true">
          <span
            data-tone={cell.tone}
            style={{
              left: `${Math.max(0, ((start - runStart) / span) * 100)}%`,
              width: `${Math.max(1.5, ((end - start) / span) * 100)}%`
            }}
          />
        </div>
      ) : null}
      {cell.detail ? <pre className="td-run-statement">{cell.detail}</pre> : null}
    </li>
  );
};

const LiveLog = ({ runId, jobId }: { runId: string; jobId: string }) => {
  const { token } = useAuth();
  const { messages, wsOpen } = useRunLiveEvents(runId, token);
  const lines = messages.filter((item) => !item.job_id || item.job_id === jobId).slice().reverse();
  return (
    <div className="td-run-live">
      <p className="td-run-live-state" data-open={wsOpen || undefined}>
        <i aria-hidden="true" /> {wsOpen ? "Connected" : "Connecting…"}
      </p>
      {lines.length ? (
        <ol>
          {lines.map((line, index) => (
            <li key={`${line.ts}-${index}`} data-level={(line.level ?? line.status ?? "").toLowerCase()}>
              <time>{line.ts ? new Date(line.ts).toLocaleTimeString() : ""}</time>
              <span>{line.message ?? (line.status ? `status → ${line.status}` : line.type)}</span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="td-run-note">No log lines for this run yet.</p>
      )}
    </div>
  );
};
