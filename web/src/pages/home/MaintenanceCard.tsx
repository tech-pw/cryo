import { Link } from "react-router-dom";
import { ArrowUpRight, Clock3 } from "lucide-react";
import type { RunRead } from "../../api/types";
import { Skeleton } from "../../components/ui/skeleton";
import { optionalNumber } from "../../lib/batchSummary";
import { formatNumber, formatTime } from "../../lib/format";
import { HomeCard } from "./HomeCard";

export const maintenanceProgress = (run: RunRead) => {
  const count = (key: string) => {
    const value = optionalNumber(run.summary, key);
    return value != null && Number.isInteger(value) && value >= 0 ? value : null;
  };
  const selected = count("selected");
  const succeeded = count("succeeded");
  const failed = count("failed");
  const skipped = count("skipped");
  const partial = count("partial");
  const planningFailed = count("planning_failed");
  const nothingToDo = count("nothing_to_do");
  const outcomes = [succeeded, failed, skipped, partial, planningFailed, nothingToDo];
  // Locked tables are already counted in skipped; over-budget tables were never selected.
  const complete = outcomes.every((value) => value != null) ? outcomes.reduce<number>((sum, value) => sum + (value ?? 0), 0) : null;
  const measurable = selected != null && selected > 0 && complete != null && complete <= selected;
  return { selected, succeeded, failed, skipped, partial, planningFailed, nothingToDo, complete, percent: measurable ? Math.floor(complete / selected * 100) : null };
};

export const MaintenanceCard = ({ run, loading, error }: { run: RunRead | null; loading: boolean; error: boolean }) => {
  const progress = run ? maintenanceProgress(run) : null;
  const status = run?.status?.replaceAll("_", " ") ?? "Unknown";
  const ended = Boolean(run?.finished_at);
  return (
    <HomeCard title="Latest nightly maintenance" description="Latest reported outcomes for the nightly batch." action={{ label: "All runs", href: "/runs" }} busy={loading} className="home-maintenance">
      {loading ? <Skeleton className="h-40 w-full" /> : error ? <p className="home-empty">Nightly run unavailable</p> : !run || !progress ? <div className="home-empty"><Clock3 size={22} aria-hidden="true" /><p>No nightly run</p><Link className="home-text-link" to="/runs">View maintenance runs <ArrowUpRight size={14} aria-hidden="true" /></Link></div> : <>
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
        <div className="home-card-footer"><span className="home-caption">{progress.percent == null ? "Open the run for job progress while the summary is pending." : "Each color represents a reported outcome."}</span><Link className="home-text-link" to={`/runs/${run.id}`}>Inspect run <ArrowUpRight size={14} aria-hidden="true" /></Link></div>
      </>}
    </HomeCard>
  );
};
