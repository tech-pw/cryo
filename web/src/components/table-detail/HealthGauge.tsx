interface HealthGaugeProps {
  score?: number | null;
  grade?: string | null;
  trendLabel?: string | null;
  trendDirection?: "up" | "down" | "flat";
  daysToCritical?: number | null;
}

const letterFromScore = (score: number): string => {
  if (score >= 90) return "A";
  if (score >= 75) return "B";
  if (score >= 60) return "C";
  if (score >= 40) return "D";
  return "F";
};

export const HealthGauge = ({ score, grade }: HealthGaugeProps) => {
  const explicitGrade = typeof grade === "string" ? grade.trim() : grade;
  const gradeMissing = explicitGrade == null || explicitGrade === "";
  const scoreFinite = score != null && Number.isFinite(score);
  const letter = gradeMissing ? (grade === undefined && scoreFinite ? letterFromScore(score) : null) : explicitGrade;

  if (!letter) {
    return (
      <div className="td-health-gauge" role="status">
        <span className="td-health-gauge-label">Not scanned</span>
      </div>
    );
  }

  const integer = scoreFinite ? Math.round(score) : null;

  return (
    <div
      className="td-health-gauge"
      role="group"
      aria-label={integer == null ? `Grade ${letter}` : `Grade ${letter}, score ${integer}`}
    >
      <span className="td-health-gauge-score">{letter}</span>
      <span className="td-health-gauge-label">{integer == null ? "Not measured" : integer}</span>
    </div>
  );
};
