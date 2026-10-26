import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, CheckCircle2, Circle, Loader2, MinusCircle, Play, RefreshCw, SlidersHorizontal, XCircle } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getTableAudit, getTableRuns, getTableSkipEvaluations } from "../../../api/client";
import { useRunLiveEvents } from "../../../hooks/useRunLiveEvents";
import { useAuth } from "../../../lib/auth";
import { formatBytesGb, formatNumber, formatTime } from "../../../lib/format";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import { QueryErrorAlert } from "../../common";
import { Button } from "../../ui/button";
import { Skeleton } from "../../ui/skeleton";
import {
  OPERATION_SHORT,
  buildRunColumns,
  changeColumns,
  formatMs,
  gradeChange,
  gridRows,
  isActiveTone,
  mergeSkipColumns,
  skipReasonText,
  statChanges,
  toneFor,
  type CellTone,
  type ChangeColumn,
  type GridColumn,
  type OpCell,
  type RunColumn,
  type SkipColumn
} from "./runModel";
import "./runs.css";

const RUNS_LIMIT = 25;
const AUDIT_LIMIT = 200;
const SKIP_LIMIT = 120;
const LIVE_LOG_WINDOW_MS = 24 * 60 * 60 * 1000;

type Filter = "all" | "manual" | "nightly";

const TONE_LABEL: Record<CellTone, string> = {
  success: "Succeeded",
  failed: "Failed",
  attention: "Partial",
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

const manualTriggerLabel = (trigger?: string | null): string =>
  !trigger || trigger === "manual" ? "Manual" : `Manual · ${trigger.replace(/[-_]/g, " ")}`;

const skipRange = (column: SkipColumn) =>
  column.count === 1 ? shortDate(column.lastAt) : `${shortDate(column.firstAt)} – ${shortDate(column.lastAt)}`;

const ROUTINE_CHANGE_STATUS = new Set(["recorded", "ok", "succeeded"]);
const notableStatus = (status: string) => Boolean(status) && !ROUTINE_CHANGE_STATUS.has(status.toLowerCase());

const changeLabel = (column: ChangeColumn) =>
  column.entries.length === 1
    ? `${column.label} · ${formatTime(column.startedAt)}`
    : `${column.entries.length} config changes · ${shortDate(column.entries[0].startedAt)} – ${shortDate(column.startedAt)}`;

const skipLabel = (column: SkipColumn) =>
  `Not selected ${skipRange(column)}, ${skipReasonText(column.reason).title}${column.count > 1 ? `, ${column.count} nights` : ""}`;

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

  const skips = useQuery({
    queryKey: tableKeys.skipEvaluations(tableId, token, SKIP_LIMIT),
    queryFn: () => getTableSkipEvaluations(api, tableId, { limit: SKIP_LIMIT }),
    enabled: tableId.length > 0
  });

  const allColumns = useMemo(() => buildRunColumns(runs.data ?? [], audit.data ?? []), [runs.data, audit.data]);
  const changes = useMemo(() => changeColumns(audit.data ?? []), [audit.data]);
  const columns: GridColumn[] = useMemo(() => {
    const shownRuns = allColumns.filter((column) => filter === "all" || (filter === "manual" ? column.manual : !column.manual));
    const shownChanges = filter === "nightly" ? [] : changes;
    const evaluations = filter === "manual" ? [] : (skips.data?.items ?? []);
    return mergeSkipColumns(shownRuns, evaluations, 12, shownChanges);
  }, [filter, allColumns, changes, skips.data]);
  const runColumns = columns.filter((column): column is RunColumn => column.kind === "run");
  const rows = gridRows(columns);
  const peak = Math.max(1, ...runColumns.map((column) => column.durationMs ?? 0));
  const requestedRun = searchParams.get("run");
  const selected =
    columns.find(
      (column) =>
        column.key === requestedRun ||
        (column.kind === "run" && column.run.run_id === requestedRun) ||
        (column.kind === "change" && column.entries.some((entry) => entry.key === requestedRun))
    ) ??
    [...columns].reverse().find((column) => column.kind === "run") ??
    columns[columns.length - 1] ??
    null;
  const select = (column: GridColumn) => {
    const next = new URLSearchParams(searchParams);
    next.set("run", column.kind === "run" ? column.run.run_id : column.key);
    setSearchParams(next, { replace: true });
  };
  const counts = {
    success: allColumns.filter((column) => column.tone === "success").length,
    failed: allColumns.filter((column) => column.tone === "failed").length,
    active: allColumns.filter((column) => isActiveTone(column.tone)).length
  };
  const shownChanges = columns.filter((column): column is ChangeColumn => column.kind === "change");
  const changeCount = shownChanges.reduce((sum, column) => sum + column.entries.length, 0);
  const shownSkips = columns.filter((column): column is SkipColumn => column.kind === "skip");
  const skippedWithWork = shownSkips.filter((column) => column.hadWork).reduce((sum, column) => sum + column.count, 0);

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
            {skippedWithWork ? (
              <>
                {" "}
                · <span className="td-runs-count" data-tone="attention">
                  passed over {skippedWithWork} night{skippedWithWork === 1 ? "" : "s"} with work pending
                </span>
              </>
            ) : null}
            {changeCount ? <> · {changeCount} config change{changeCount === 1 ? "" : "s"}</> : null}
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
            <div
              className="td-grid"
              style={{
                gridTemplateColumns: `minmax(112px, max-content) ${columns.map((column) => (column.kind === "change" ? "14px" : "20px")).join(" ")}`
              }}
            >
              <div className="td-grid-label td-grid-label--bars">Duration</div>
              {columns.map((column) =>
                column.kind === "change" ? (
                  <button
                    key={`bar-${column.key}`}
                    type="button"
                    className="td-grid-bar td-grid-bar--change"
                    data-selected={selected?.key === column.key || undefined}
                    aria-label={changeLabel(column)}
                    title={changeLabel(column)}
                    onClick={() => select(column)}
                  >
                    <span className="td-grid-change-badge">
                      {column.entries.length > 1 ? column.entries.length : <SlidersHorizontal size={9} strokeWidth={2.5} aria-hidden="true" />}
                    </span>
                  </button>
                ) : column.kind === "skip" ? (
                  <button
                    key={`bar-${column.key}`}
                    type="button"
                    className="td-grid-bar td-grid-bar--passed"
                    data-work={column.hadWork || undefined}
                    data-selected={selected?.key === column.key || undefined}
                    aria-label={skipLabel(column)}
                    title={skipLabel(column)}
                    onClick={() => select(column)}
                  >
                    {column.count > 1 ? <em>{column.count}</em> : <span />}
                  </button>
                ) : (
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
                  <span style={{ height: `${Math.max(8, ((column.durationMs ?? 0) / peak) * 100)}%` }} />
                  {column.manual ? <i className="td-grid-manual" aria-hidden="true" /> : null}
                </button>
                )
              )}
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
            {shownChanges.length ? (
              <li className="td-grid-legend-change">
                <span className="td-grid-change-badge" aria-hidden="true">
                  <SlidersHorizontal size={9} strokeWidth={2.5} />
                </span>
                Config change
              </li>
            ) : null}
            {shownSkips.length ? (
              <li className="td-grid-legend-passed">
                <i aria-hidden="true" />
                Not selected
              </li>
            ) : null}
          </ul>
          {selected?.kind === "skip" ? (
            <SkipDetailPanel column={selected} />
          ) : selected?.kind === "change" ? (
            <ChangeDetailPanel column={selected} />
          ) : selected ? (
            <RunDetailPanel column={selected} />
          ) : null}
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
  columns: GridColumn[];
  selectedKey: string | null;
  onSelect: (column: GridColumn) => void;
}) => (
  <>
    <div className="td-grid-label">{OPERATION_SHORT[op] ?? op.replaceAll("_", " ")}</div>
    {columns.map((column) => {
      if (column.kind === "change") {
        return (
          <button
            key={`${op}-${column.key}`}
            type="button"
            className="td-grid-cell td-grid-cell--change"
            data-selected={selectedKey === column.key || undefined}
            aria-label={changeLabel(column)}
            title={changeLabel(column)}
            onClick={() => onSelect(column)}
          />
        );
      }
      if (column.kind === "skip") {
        return (
          <button
            key={`${op}-${column.key}`}
            type="button"
            className="td-grid-cell td-grid-cell--passed"
            data-work={column.hadWork || undefined}
            data-selected={selectedKey === column.key || undefined}
            aria-label={skipLabel(column)}
            title={skipLabel(column)}
            onClick={() => onSelect(column)}
          />
        );
      }
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

const SKIP_NEXT_STEP: Record<string, string> = {
  stale_stats: "Rescan the table, or check why the scheduled scan is failing.",
  excluded: "Check that the table and its source are active and the schema is in the maintenance allow-list.",
  held: "See the hold's reason and expiry in table settings. Release it there to let tonight's run pick the table.",
  backoff: "Open the last failed run to see the error. Cryo retries on its own after the backoff.",
  budget_cap: "Raise the scheduled run's table cap, or run this table on demand."
};

const SkipDetailPanel = ({ column }: { column: SkipColumn }) => {
  const reason = skipReasonText(column.reason);
  const next = column.reason ? SKIP_NEXT_STEP[column.reason] : undefined;
  return (
    <div className="td-run-detail td-run-detail--passed">
      <div className="td-run-summary">
        <p className="td-run-status" data-tone={column.hadWork ? "attention" : undefined}>
          <MinusCircle size={14} aria-hidden="true" /> Not selected · {reason.title}
        </p>
        <dl>
          <div>
            <dt>{column.count === 1 ? "Night" : "Nights"}</dt>
            <dd>{skipRange(column)}</dd>
          </div>
          <div>
            <dt>Times passed over</dt>
            <dd>{column.count}</dd>
          </div>
          <div>
            <dt>Work pending</dt>
            <dd>{column.hadWork ? "Yes, the last scan found work" : "No"}</dd>
          </div>
          <div>
            <dt>Trigger</dt>
            <dd>Nightly schedule</dd>
          </div>
        </dl>
      </div>
      <div className="td-run-passed-why">
        <p className="td-run-subhead">Why</p>
        <p>{reason.detail}</p>
        {next ? (
          <>
            <p className="td-run-subhead">What to do</p>
            <p>{next}</p>
          </>
        ) : null}
      </div>
    </div>
  );
};

const ChangeDetailPanel = ({ column }: { column: ChangeColumn }) => (
  <div className="td-run-detail td-run-detail--change">
    <div className="td-run-summary">
      <p className="td-run-status">
        <SlidersHorizontal size={14} aria-hidden="true" />{" "}
        {column.entries.length === 1 ? column.label : `${column.entries.length} config changes between runs`}
      </p>
      <ol className="td-change-list">
        {[...column.entries].reverse().map((entry) => (
          <li key={entry.key}>
            <dl>
              <div>
                <dt>When</dt>
                <dd>{formatTime(entry.startedAt)}</dd>
              </div>
              <div>
                <dt>By</dt>
                <dd>{entry.actor ?? "Not recorded"}</dd>
              </div>
              {column.entries.length > 1 || notableStatus(entry.status) ? (
                <div>
                  <dt>Change</dt>
                  <dd>
                    {entry.label}
                    {notableStatus(entry.status) ? ` · ${entry.status}` : ""}
                  </dd>
                </div>
              ) : null}
            </dl>
            {entry.detail ? <pre className="td-run-statement">{entry.detail}</pre> : null}
          </li>
        ))}
      </ol>
    </div>
  </div>
);

const RunDetailPanel = ({ column }: { column: RunColumn }) => {
  const [tab, setTab] = useState<"steps" | "live">("steps");
  const run = column.run;
  const changes = statChanges(run);
  const grade = gradeChange(run);
  const hasAfter = Boolean(run.after_stats);
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
            <dd>{column.manual ? manualTriggerLabel(run.trigger) : "Nightly schedule"}</dd>
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
            <p className="td-run-subhead">{hasAfter ? "Before this run → next scan after" : "Table before this run"}</p>
            <table>
              <thead>
                <tr>
                  <th scope="col">
                    <span className="sr-only">Metric</span>
                  </th>
                  <th scope="col">Before</th>
                  <th scope="col" aria-hidden="true" />
                  <th scope="col">After</th>
                  <th scope="col">
                    <span className="sr-only">Change</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {grade ? (
                  <tr>
                    <th scope="row">Grade</th>
                    <td>{grade.before ?? "—"}</td>
                    <td aria-hidden="true">→</td>
                    <td>
                      <b>{grade.after ?? "—"}</b>
                    </td>
                    <td className="td-run-delta">{!hasAfter ? "" : grade.before === grade.after ? "no change" : ""}</td>
                  </tr>
                ) : null}
                {changes.map((change) => (
                  <ChangeRow key={change.label} change={change} pending={!hasAfter} />
                ))}
              </tbody>
            </table>
            <p className="td-run-footnote">
              {hasAfter
                ? "The after-scan can include new writes, so treat it as the table's state, not this run's effect alone."
                : isActiveTone(column.tone)
                  ? "After-stats appear once the run finishes and the table is rescanned."
                  : "After-stats appear once the table is next scanned."}
            </p>
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

const ChangeRow = ({ change, pending }: { change: ReturnType<typeof statChanges>[number]; pending: boolean }) => {
  const { before, after } = change;
  if (pending) {
    const fmtPending = (value: number | null) =>
      value == null ? "—" : change.unit === "GB" ? `${value.toFixed(1)} GB` : value.toLocaleString("en-US");
    return (
      <tr>
        <th scope="row">{change.label}</th>
        <td>
          <b>{fmtPending(before)}</b>
        </td>
        <td aria-hidden="true">→</td>
        <td className="td-run-pending">pending scan</td>
        <td />
      </tr>
    );
  }
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
      <StepEffect cell={cell} />
      {cell.detail ? <pre className="td-run-statement">{cell.detail}</pre> : null}
    </li>
  );
};

const StepEffect = ({ cell }: { cell: OpCell }) => {
  const parts: string[] = [];
  if (cell.filesIn != null && cell.filesOut != null) {
    parts.push(`${formatNumber(cell.filesIn)} → ${formatNumber(cell.filesOut)} files`);
  } else if (cell.filesIn != null) {
    parts.push(`${formatNumber(cell.filesIn)} files rewritten`);
  }
  if (cell.bytesFreed != null) parts.push(`${formatBytesGb(cell.bytesFreed)} freed`);
  if (!parts.length) {
    return cell.tone === "success" ? (
      <p className="td-run-step-effect" data-measured="false">The engine didn't report what this step changed.</p>
    ) : null;
  }
  return <p className="td-run-step-effect">{parts.join(" · ")}</p>;
};

const LiveLog = ({ runId, jobId }: { runId: string; jobId: string }) => {
  const { token } = useAuth();
  const { messages, snapshot, wsOpen, wsExhausted, runTerminal, manualReconnect } = useRunLiveEvents(runId, token);
  const jobError = snapshot?.jobs?.find((job) => job.id === jobId)?.error;
  const lines = messages.filter((item) => !item.job_id || item.job_id === jobId);
  return (
    <div className="td-run-live">
      <p className="td-run-live-state" data-open={wsOpen || undefined}>
        <i aria-hidden="true" /> {wsOpen ? "Connected" : runTerminal ? "Ended" : wsExhausted ? "Live log disconnected" : "Connecting…"}
      </p>
      {wsExhausted ? (
        <button type="button" className="text-xs underline" onClick={manualReconnect}>
          Retry
        </button>
      ) : null}
      {jobError ? <p className="td-run-note">{jobError}</p> : null}
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
