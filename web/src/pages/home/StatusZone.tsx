import { CheckCircle2, TriangleAlert } from "lucide-react";
import type { CryoVitals } from "../../api/vitals";
import { Skeleton } from "../../components/ui/skeleton";
import { formatTime } from "../../lib/format";
import { HomeCard } from "./HomeCard";

export interface StatusZoneProps {
  vitals?: CryoVitals | null;
  vitalsLoading: boolean;
  vitalsError: boolean;
}

const reasonText = (value: string) => value.includes(": ") ? value.slice(value.indexOf(": ") + 2) : value;

/** The sentence that explains the status word; never invents one when the payload has none. */
export const platformReason = (vitals: CryoVitals | null | undefined): { text: string; points: number | null } | null => {
  const top = [...(vitals?.deductions ?? [])].sort((a, b) => b.points - a.points)[0];
  if (top) return { text: reasonText(top.reason), points: top.points };
  if (vitals?.status && vitals.status !== "HEALTHY" && vitals.headline) return { text: vitals.headline, points: null };
  return null;
};

export const StatusZone = ({ vitals, vitalsLoading, vitalsError }: StatusZoneProps) => {
  const score = typeof vitals?.score === "number" && Number.isFinite(vitals.score) && vitals.score >= 0 && vitals.score <= 100 ? vitals.score : null;
  const status = vitals?.status;
  const tone = status === "HEALTHY" ? "success" : status === "CRITICAL" ? "danger" : status === "DEGRADED" ? "warning" : "neutral";
  const label = status === "HEALTHY" ? "Healthy" : status === "CRITICAL" ? "Critical" : status === "DEGRADED" ? "Needs attention" : "Status unavailable";
  const deductions = [...(vitals?.deductions ?? [])].sort((a, b) => b.points - a.points);
  const reason = platformReason(vitals);
  return (
    <HomeCard id="home-platform" title="Cryo platform" description="Health of Cryo itself: dispatch, jobs, scanner and checks." className="home-platform" busy={vitalsLoading}>
      {vitalsLoading ? <Skeleton className="h-36 w-full" /> : vitalsError ? <p className="home-empty">Vitals unavailable</p> : score == null ? <p className="home-empty">Platform health is not measured yet.</p> : (
        <div className="home-platform-body">
          <div className={`home-gauge home-tone-${tone}`}>
            <svg viewBox="0 0 200 122" role="img" aria-label={`Cryo platform health: ${score} out of 100. ${label}.`}>
              <path d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke="var(--color-chart-grid)" strokeWidth="13" strokeLinecap="round" />
              <path className="home-gauge-value" d="M 20 100 A 80 80 0 0 1 180 100" pathLength="100" fill="none" stroke="currentColor" strokeWidth="13" strokeLinecap={score === 0 ? "butt" : "round"} strokeDasharray={`${score} 100`} />
              <text x="19" y="120" textAnchor="middle" className="home-gauge-bound">0</text><text x="181" y="120" textAnchor="middle" className="home-gauge-bound">100</text>
            </svg>
            <div className="home-gauge-label" aria-hidden="true"><strong>{score}</strong><span>/ 100</span></div>
          </div>
          <div className="home-platform-detail">
            <div className={`home-platform-status home-tone-${tone}`}>{tone === "success" ? <CheckCircle2 size={16} aria-hidden="true" /> : <TriangleAlert size={16} aria-hidden="true" />}<strong>{label}</strong></div>
            {reason ? <p className="home-platform-reason">{reason.text}{reason.points != null ? <span className="home-caption"> (−{reason.points} points)</span> : null}</p> : null}
            {deductions.length > 1 ? <details className="home-platform-more"><summary>{deductions.length - 1} more health signals</summary><ul>{deductions.slice(1).map((row) => <li key={row.reason}>{reasonText(row.reason)} (−{row.points})</li>)}</ul></details> : null}
            {vitals?.generated_at ? <p className="home-caption">Evaluated {formatTime(vitals.generated_at)}</p> : null}
          </div>
        </div>
      )}
    </HomeCard>
  );
};
