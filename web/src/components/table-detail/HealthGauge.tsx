import { ArrowDown, ArrowUp } from "lucide-react";
import { healthScoreColor } from "../status";
import { Badge } from "../ui/badge";

interface HealthGaugeProps {
  score: number;
  trendLabel?: string | null;
  trendDirection?: "up" | "down" | "flat";
  daysToCritical?: number | null;
}

const RADIUS = 52;
const CIRCUMFERENCE = Math.PI * RADIUS;

const clampScore = (score: number) => Math.max(0, Math.min(100, Number.isFinite(score) ? score : 0));

export const HealthGauge = ({
  score,
  trendLabel,
  trendDirection = "flat",
  daysToCritical
}: HealthGaugeProps) => {
  const safeScore = clampScore(score);
  const color = healthScoreColor(safeScore);
  const progress = (safeScore / 100) * CIRCUMFERENCE;

  return (
    <div className="td-health-gauge">
      <svg
        viewBox="0 0 140 78"
        className="td-health-gauge-svg"
        role="img"
        aria-label={`Health score ${safeScore.toFixed(0)}`}
      >
        <path
          d={`M 18 70 A ${RADIUS} ${RADIUS} 0 0 1 122 70`}
          className="td-health-gauge-track"
          pathLength={CIRCUMFERENCE}
        />
        <path
          d={`M 18 70 A ${RADIUS} ${RADIUS} 0 0 1 122 70`}
          className="td-health-gauge-progress"
          stroke={color}
          strokeDasharray={`${progress} ${CIRCUMFERENCE}`}
          pathLength={CIRCUMFERENCE}
        />
      </svg>

      <div className="td-health-gauge-center">
        <span className="td-health-gauge-score">{safeScore.toFixed(0)}</span>
        <span className="td-health-gauge-label">Overall Health</span>
      </div>

      <div className="td-health-gauge-meta">
        {trendLabel ? (
          <span className={`td-health-trend td-health-trend-${trendDirection}`}>
            {trendDirection === "up" ? <ArrowUp size={12} /> : null}
            {trendDirection === "down" ? <ArrowDown size={12} /> : null}
            {trendLabel}
          </span>
        ) : (
          <span className="td-health-trend td-health-trend-flat">No Trend Signal Yet</span>
        )}
        {daysToCritical != null && daysToCritical < 30 ? (
          <Badge variant="warning" className="td-health-critical-tag">
            {daysToCritical} Days to Critical
          </Badge>
        ) : null}
      </div>
    </div>
  );
};
