import { Link } from "react-router-dom";
import { ArrowUpRight, Clock3 } from "lucide-react";
import type { RunRead } from "../../api/types";
import { Skeleton } from "../../components/ui/skeleton";
import { formatNumber, formatTime } from "../../lib/format";
import { HomeCard } from "./HomeCard";
import { maintenanceProgress, nightOutcome, type NightOutcome, type OffPeakState } from "./homeSignals";

export { maintenanceProgress };

const HISTORY_SLOTS = 14;
const OUTCOME_LABEL: Record<NightOutcome, string> = {
  succeeded: "Succeeded",
  partial: "Partial",
  failed: "Had failures",
  running: "In progress",
  unknown: "Outcome not reported"
};

const NightHistory = ({ runs }: { runs: readonly RunRead[] }) => {
  const nights = runs.slice(0, HISTORY_SLOTS).reverse();
  const finished = nights.filter((run) => nightOutcome(run) !== "running" && nightOutcome(run) !== "unknown");
  const clean = finished.filter((run) => nightOutcome(run) === "succeeded").length;
  const empty = HISTORY_SLOTS - nights.length;
  return (
    <div className="home-night-history">
      <div className="home-night-history-head">
        <span className="home-caption">Last {HISTORY_SLOTS} nights</span>
        {finished.length ? <span className="home-caption"><strong>{clean}</strong> of {finished.length} finished without failures</span> : null}
      </div>
      <ol aria-label="Nightly run history, oldest first">
        {Array.from({ length: empty }, (_, index) => <li key={`empty-${index}`} className="home-night home-night-none" aria-hidden="true" />)}
        {nights.map((run) => {
          const outcome = nightOutcome(run);
          const label = `${formatTime(run.created_at)}: ${OUTCOME_LABEL[outcome]}`;
          return (
            <li key={run.id} className={`home-night home-night-${outcome}`}>
              <Link to={`/runs/${run.id}`} aria-label={label} title={label} />
            </li>
          );
        })}
      </ol>
    </div>
  );
};

export const MaintenanceCard = ({ run, loading, error, history = [], offPeak = null }: {
  run: RunRead | null;
  loading: boolean;
  error: boolean;
  history?: readonly RunRead[];
  offPeak?: OffPeakState | null;
}) => {
  const progress = run ? maintenanceProgress(run) : null;
  const status = run?.status?.replaceAll("_", " ") ?? "Unknown";
  const ended = Boolean(run?.finished_at);
  return (
    <HomeCard title="Latest nightly maintenance" description="What the nightly batch did, and how the last two weeks went." action={{ label: "All runs", href: "/runs" }} busy={loading} className="home-maintenance">
      {loading ? <Skeleton className="h-40 w-full" /> : error ? <p className="home-empty">Nightly run unavailable</p> : !run || !progress ? (
        <div className="home-empty">
          <Clock3 size={22} aria-hidden="true" />
          <p>No nightly run yet</p>
          {offPeak ? <p className="home-caption">Heavy rewrites are admitted in the off-peak window, {offPeak.label}{offPeak.open ? " (open now)" : ""}.</p> : null}
          <Link className="home-text-link" to="/runs">View maintenance runs <ArrowUpRight size={14} aria-hidden="true" /></Link>
        </div>
      ) : <>
        <div className="home-run-headline">
          <div><span className="home-caption">Tables succeeded</span><p className="home-run-value">{progress.succeeded == null ? "Not reported" : <>{formatNumber(progress.succeeded)}<span> / {progress.selected == null ? "?" : formatNumber(progress.selected)} selected</span></>}</p></div>
          <div className="home-run-context"><span className={`home-run-status ${progress.failed ? "home-tone-danger" : ""}`}>{status}</span><span>{ended ? "Finished" : "Created"} {formatTime(run.finished_at ?? run.created_at)}</span></div>
        </div>
        {progress.percent != null && progress.selected != null ? <div className="home-progress-track" role="img" aria-label={`${formatNumber(progress.complete)} of ${formatNumber(progress.selected)} selected tables have outcomes, including partial work, failures and skips.`}>
          {[
            { value: progress.succeeded, color: "var(--color-status-success)" },
            { value: progress.partial, color: "var(--color-status-warning)" },
            { value: progress.failed, color: "var(--color-status-danger)" },
            { value: progress.skipped, color: "var(--color-muted-subtle)" },
            { value: (progress.planningFailed ?? 0) + (progress.nothingToDo ?? 0), color: "var(--color-chart-primary)" }
          ].map((segment, index) => <span key={index} style={{ width: `${(segment.value ?? 0) / progress.selected! * 100}%`, backgroundColor: segment.color }} />)}
        </div> : null}
        <dl className="home-run-counts">{[
          { label: "Succeeded", value: progress.succeeded, tone: "success" },
          { label: "Failed", value: progress.failed, tone: "danger" },
          { label: "Partial", value: progress.partial, tone: "warning" },
          { label: "Skipped", value: progress.skipped, tone: "neutral" }
        ].map((row) => <div key={row.label}><dt><span className={`home-dot home-dot-${row.tone}`} />{row.label}</dt><dd>{row.value == null ? "Unknown" : formatNumber(row.value)}</dd></div>)}</dl>
        <p className="home-caption home-run-detail">{progress.selected == null ? "Selected count not reported." : `${formatNumber(progress.selected)} selected.`} {progress.nothingToDo != null && progress.nothingToDo > 0 ? `${formatNumber(progress.nothingToDo)} needed no work. ` : ""}{progress.planningFailed != null && progress.planningFailed > 0 ? `${formatNumber(progress.planningFailed)} failed planning.` : ""}</p>
        {history.length > 0 ? <NightHistory runs={history} /> : null}
        <div className="home-card-footer"><span className="home-caption">{progress.percent == null ? "Open the run for job progress while the summary is pending." : "Each color represents a reported outcome."}</span><Link className="home-text-link" to={`/runs/${run.id}`}>Inspect run <ArrowUpRight size={14} aria-hidden="true" /></Link></div>
      </>}
    </HomeCard>
  );
};
