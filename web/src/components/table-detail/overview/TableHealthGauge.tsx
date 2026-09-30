const labels: Record<string, string> = { A: "Excellent", B: "Good", C: "Fair", D: "Poor", F: "Critical" };

export const TableHealthGauge = ({ score, grade }: { score: number | null; grade: string | null }) => {
  const measured = score != null && score >= 0 && score <= 100;
  const knownGrade = grade != null && Object.hasOwn(labels, grade);
  const color = knownGrade ? `var(--grade-${grade.toLowerCase()})` : "var(--color-muted-subtle)";
  return (
    <div className="td-score-gauge">
      <svg viewBox="0 0 200 126" role="img" aria-label={measured ? `Table health score ${score} out of 100${grade ? `, grade ${grade}` : ""}` : "Table health score not recorded"}>
        <path d="M 20 104 A 80 80 0 0 1 180 104" stroke="var(--color-chart-grid)" strokeWidth="13" strokeLinecap="round" fill="none" />
        {measured ? <path d="M 20 104 A 80 80 0 0 1 180 104" pathLength="100" stroke={color} strokeWidth="13" strokeLinecap={score === 0 ? "butt" : "round"} strokeDasharray={`${score} 100`} fill="none" className="td-score-arc" /> : null}
        <text x="20" y="125" textAnchor="middle">0</text><text x="180" y="125" textAnchor="middle">100</text>
      </svg>
      <div className="td-score-gauge-value" aria-hidden="true"><strong>{measured ? score : "—"}</strong><span>/ 100</span></div>
      <p><span className="td-grade-key" style={{ borderColor: color }}>{grade ?? "—"}</span>{knownGrade ? labels[grade] : "Not graded"}</p>
    </div>
  );
};
