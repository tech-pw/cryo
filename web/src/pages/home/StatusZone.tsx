import { Link } from "react-router-dom";
import { ArrowUpRight, CheckCircle2, TriangleAlert } from "lucide-react";
import type { CryoVitals } from "../../api/vitals";
import { Skeleton } from "../../components/ui/skeleton";
import { formatNumber, formatTime } from "../../lib/format";
import { HomeCard } from "./HomeCard";

export interface StatusZoneProps {
  vitals?: CryoVitals | null;
  vitalsLoading: boolean;
  vitalsError: boolean;
  scannerLabel: string | null;
  queueDepth: number | null;
  engineLabel: string | null;
}

export const StatusZone = ({ vitals, vitalsLoading, vitalsError, scannerLabel, queueDepth, engineLabel }: StatusZoneProps) => {
  const score = typeof vitals?.score === "number" && Number.isFinite(vitals.score) && vitals.score >= 0 && vitals.score <= 100 ? vitals.score : null;
  const status = vitals?.status;
  const tone = status === "HEALTHY" ? "success" : status === "CRITICAL" ? "danger" : status === "DEGRADED" ? "warning" : "neutral";
  const label = status === "HEALTHY" ? "Healthy" : status === "CRITICAL" ? "Critical" : status === "DEGRADED" ? "Needs attention" : "Status unavailable";
  const deductions = [...(vitals?.deductions ?? [])].sort((a, b) => b.points - a.points);
  const reason = (value: string) => value.includes(": ") ? value.slice(value.indexOf(": ") + 2) : value;
  return (
    <HomeCard title="Cryo platform" description="Service health and maintenance readiness." className="home-platform" busy={vitalsLoading}>
      {vitalsLoading ? <Skeleton className="h-44 w-full" /> : vitalsError ? <p className="home-empty">Vitals unavailable</p> : score == null ? <p className="home-empty">Platform health is not measured yet.</p> : (
        <div className="home-vitals-visual">
          <div className={`home-gauge home-tone-${tone}`}>
            <svg viewBox="0 0 200 122" role="img" aria-label={`Cryo platform health: ${score} out of 100. ${label}.`}>
              <path d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke="var(--color-chart-grid)" strokeWidth="13" strokeLinecap="round" />
              <path className="home-gauge-value" d="M 20 100 A 80 80 0 0 1 180 100" pathLength="100" fill="none" stroke="currentColor" strokeWidth="13" strokeLinecap={score === 0 ? "butt" : "round"} strokeDasharray={`${score} 100`} />
              <text x="19" y="120" textAnchor="middle" className="home-gauge-bound">0</text><text x="181" y="120" textAnchor="middle" className="home-gauge-bound">100</text>
            </svg>
            <div className="home-gauge-label" aria-hidden="true"><strong>{score}</strong><span>/ 100</span></div>
          </div>
          <div className={`home-platform-status home-tone-${tone}`}>{tone === "success" ? <CheckCircle2 size={16} aria-hidden="true" /> : <TriangleAlert size={16} aria-hidden="true" />}<strong>{label}</strong></div>
          {vitals?.generated_at ? <p className="home-caption">Evaluated {formatTime(vitals.generated_at)}</p> : null}
        </div>
      )}
      {!vitalsError && !vitalsLoading && deductions.length > 0 ? <div className="home-deduction">
        <p>{reason(deductions[0].reason)} <span className="home-caption">(−{deductions[0].points} points)</span></p>
        {deductions.length > 1 ? <details><summary>{deductions.length - 1} more health signals</summary><ul>{deductions.slice(1).map((row) => <li key={row.reason}>{reason(row.reason)} (−{row.points})</li>)}</ul></details> : null}
      </div> : null}
      <nav aria-label="Platform" className="home-platform-links">
        {[
          { label: "Scanner", value: scannerLabel ?? "Loading…", href: "/runs/scanner" },
          { label: "Scan queue", value: queueDepth == null ? "Unavailable" : `${formatNumber(queueDepth)} waiting`, href: "/runs/scanner" },
          { label: "Engines", value: engineLabel ?? "Loading…", href: "/runs" }
        ].map((row) => <Link key={row.label} to={row.href} aria-label={`${row.label}: ${row.value}`}><span>{row.label}</span><strong>{row.value}<ArrowUpRight size={13} aria-hidden="true" /></strong></Link>)}
      </nav>
    </HomeCard>
  );
};
