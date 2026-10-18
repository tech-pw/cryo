import type { ReactNode } from "react";
import { ArrowRight, CheckCircle2, Flame, FileCheck2, Layers, OctagonPause, Radar, ScanSearch, ShieldAlert, TriangleAlert } from "lucide-react";
import { Link } from "react-router-dom";
import type { MaintenanceControlRead } from "../../api/types";
import { Skeleton } from "../../components/ui/skeleton";
import { formatNumber } from "../../lib/format";
import { HomeCard } from "./HomeCard";

type Tone = "danger" | "warning" | "neutral";

interface Step {
  key: string;
  icon: ReactNode;
  title: string;
  detail: string;
  href: string;
  tone: Tone;
}

export interface AttentionCardProps {
  pauses: readonly MaintenanceControlRead[] | null;
  platformCritical: string | null;
  approvals: number | null;
  approvalsLoading: boolean;
  approvalsError: boolean;
  needsCompaction: number | null;
  hotAtRisk: number | null;
  failed: number | null;
  runHref: string | null;
  dashboardLoading: boolean;
  scannerProblem: string | null;
  flaggedTables: number;
}

const plural = (count: number, one: string, many: string) => `${formatNumber(count)} ${count === 1 ? one : many}`;

export const AttentionCard = ({ pauses, platformCritical, approvals, approvalsLoading, approvalsError, needsCompaction, hotAtRisk, failed, runHref, dashboardLoading, scannerProblem, flaggedTables }: AttentionCardProps) => {
  const steps: Step[] = [];
  const global = pauses?.find((pause) => pause.scope_type === "global");
  const scoped = pauses?.filter((pause) => pause.scope_type !== "global") ?? [];
  if (global) steps.push({ key: "global-pause", icon: <OctagonPause size={17} />, title: "Maintenance is paused globally", detail: global.reason ? `${global.reason} — ${global.changed_by}` : `Paused by ${global.changed_by}. Nothing is admitted until it resumes.`, href: "/runs?tab=holds", tone: "danger" });
  if (platformCritical) steps.push({ key: "platform", icon: <ShieldAlert size={17} />, title: "Cryo platform is critical", detail: platformCritical, href: "#home-platform", tone: "danger" });
  if (failed != null && failed > 0 && runHref) steps.push({ key: "failed", icon: <TriangleAlert size={17} />, title: `${plural(failed, "table", "tables")} failed in the latest nightly run`, detail: "Read the failure details before retrying.", href: runHref, tone: "danger" });
  if (scoped.length > 0) steps.push({ key: "scoped-pause", icon: <OctagonPause size={17} />, title: `${plural(scoped.length, "maintenance hold", "maintenance holds")} active`, detail: scoped[0].reason ?? `${scoped[0].scope_type} ${scoped[0].scope_id} is on hold.`, href: "/runs?tab=holds", tone: "warning" });
  if (approvals != null && approvals > 0) steps.push({ key: "approvals", icon: <FileCheck2 size={17} />, title: `${plural(approvals, "operation approval", "operation approvals")} awaiting review`, detail: "Review proposed actions before they run.", href: "/decisions?tab=approvals#operation-approvals", tone: "warning" });
  if (scannerProblem) steps.push({ key: "scanner", icon: <ScanSearch size={17} />, title: "The scanner needs a look", detail: scannerProblem, href: "/runs/scanner", tone: "warning" });
  if (hotAtRisk != null && hotAtRisk > 0) steps.push({ key: "hot", icon: <Flame size={17} />, title: `${plural(hotAtRisk, "queried table is", "queried tables are")} at D or F`, detail: "These cost query time every day. Fix them first.", href: "#home-hot", tone: "warning" });
  if (needsCompaction != null && needsCompaction > 0) steps.push({ key: "compaction", icon: <Layers size={17} />, title: `${plural(needsCompaction, "table needs", "tables need")} compaction`, detail: "Inspect small files and choose a maintenance plan.", href: "/tables?needs=compaction", tone: "neutral" });
  if (flaggedTables > 0) steps.push({ key: "flags", icon: <Radar size={17} />, title: `${plural(flaggedTables, "new table", "new tables")} flagged early`, detail: "Fast-path checks found small files or a missing target size.", href: "#home-discovered", tone: "neutral" });

  const loading = approvalsLoading || dashboardLoading;
  return (
    <HomeCard title="Your next steps" description="What needs an operator, most urgent first." className="home-attention">
      <ul className="home-attention-list">
        {steps.map((step) => {
          const body = <>
            <span className="home-attention-icon" aria-hidden="true">{step.icon}</span>
            <div><strong>{step.title}</strong><p>{step.detail}</p></div>
            <ArrowRight size={16} aria-hidden="true" />
          </>;
          return (
            <li key={step.key}>
              {step.href.startsWith("#")
                ? <a href={step.href} className={`home-attention-${step.tone}`}>{body}</a>
                : <Link to={step.href} className={`home-attention-${step.tone}`}>{body}</Link>}
            </li>
          );
        })}
        {approvalsError ? <li className="py-3"><p className="home-caption">Approval status unavailable. <Link className="home-text-link" to="/decisions?tab=approvals#operation-approvals">Check decisions</Link></p></li> : null}
        {!dashboardLoading && needsCompaction == null ? <li className="py-3"><p className="home-caption">Compaction need is not measured.</p></li> : null}
        {loading ? <li><Skeleton className="my-4 h-12 w-full" /></li> : null}
        {!loading && steps.length === 0 && !approvalsError ? (
          <li className="home-all-clear">
            <CheckCircle2 size={18} aria-hidden="true" />
            <div><strong>All clear</strong><p>Nothing needs you right now. Cryo keeps maintaining on schedule.</p></div>
          </li>
        ) : null}
      </ul>
    </HomeCard>
  );
};
