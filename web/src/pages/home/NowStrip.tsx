import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { EngineLoadRow } from "../../api/engineLoad";
import type { MaintenanceControlRead, RunRead } from "../../api/types";
import { formatNumber } from "../../lib/format";
import type { OffPeakState } from "./homeSignals";

const ENGINES_SHOWN = 2;

export interface NowStripProps {
  pauses: readonly MaintenanceControlRead[] | null;
  activeRun: RunRead | null;
  runningJobs: number | null;
  engines: readonly EngineLoadRow[] | null;
  enginesError: boolean;
  workersOnline: number | null | undefined;
  offPeak: OffPeakState | null;
  scannerLabel: string | null;
  queueDepth: number | null;
  lastScanAgo: string | null;
  scanStale: boolean;
}

type Tone = "neutral" | "success" | "warning" | "danger";

const Clause = ({ label, children, href, tone = "neutral" }: { label: string; children: ReactNode; href?: string; tone?: Tone }) => {
  const body = <><span className="home-now-label">{label}</span><span className={`home-now-value home-now-${tone}`}>{children}</span></>;
  return <li>{href ? <Link to={href}>{body}</Link> : <span className="home-now-static">{body}</span>}</li>;
};

export const NowStrip = ({ pauses, activeRun, runningJobs, engines, enginesError, workersOnline, offPeak, scannerLabel, queueDepth, lastScanAgo, scanStale }: NowStripProps) => {
  const globalPause = pauses?.some((pause) => pause.scope_type === "global") ?? false;
  const scopedPauses = pauses?.filter((pause) => pause.scope_type !== "global").length ?? 0;
  const maintenance = globalPause
    ? { text: "Paused globally", tone: "danger" as const, href: "/runs?tab=holds" }
    : activeRun
      ? { text: runningJobs ? `Running · ${formatNumber(runningJobs)} ${runningJobs === 1 ? "job" : "jobs"}` : "Running", tone: "success" as const, href: `/runs/${activeRun.id}` }
      : runningJobs
        ? { text: `${formatNumber(runningJobs)} ${runningJobs === 1 ? "job" : "jobs"} running`, tone: "success" as const, href: "/runs" }
        : { text: "Idle", tone: "neutral" as const, href: "/runs" };
  const shown = engines?.slice(0, ENGINES_SHOWN) ?? [];
  const hidden = (engines?.length ?? 0) - shown.length;
  const throttled = engines?.some((engine) => engine.admission === "throttled") ?? false;
  const scannerText = [scannerLabel ?? "Loading…", lastScanAgo ? `scanned ${lastScanAgo}` : null, queueDepth ? `${formatNumber(queueDepth)} waiting` : null].filter(Boolean).join(" · ");
  return (
    <nav aria-label="Platform" className="home-now">
      <ul>
        <Clause label="Maintenance" href={maintenance.href} tone={maintenance.tone}>
          {maintenance.text}{scopedPauses > 0 && !globalPause ? ` · ${formatNumber(scopedPauses)} ${scopedPauses === 1 ? "hold" : "holds"}` : ""}
        </Clause>
        <Clause label="Engine slots" href="/runs" tone={throttled ? "warning" : "neutral"}>
          {enginesError ? "Unavailable" : engines == null ? "Loading…" : engines.length === 0 ? "None configured" : <>
            {shown.map((engine, index) => <span key={engine.destination_id} className="home-now-engine">{index > 0 ? " · " : ""}{engine.name} <strong>{formatNumber(engine.slots_used)}/{formatNumber(engine.slots_limit)}</strong></span>)}
            {hidden > 0 ? ` · +${formatNumber(hidden)}` : ""}
          </>}
        </Clause>
        <Clause label="Workers" href="/runs" tone={workersOnline === 0 ? "warning" : "neutral"}>
          {workersOnline == null ? "Unavailable" : workersOnline === 0 ? "None online" : `${formatNumber(workersOnline)} online`}
        </Clause>
        {offPeak ? <Clause label="Off-peak window" tone={offPeak.open ? "success" : "neutral"}>{offPeak.label}{offPeak.open ? " · open" : ""}</Clause> : null}
        <Clause label="Scanner" href="/runs/scanner" tone={scannerLabel === "Last scan failed" || scannerLabel === "Off" || scanStale ? "warning" : "neutral"}>
          {scannerText}
        </Clause>
      </ul>
    </nav>
  );
};
