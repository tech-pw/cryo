import { Link } from "react-router-dom";
import { cryoColors } from "../lib/theme";

const GRADE_ORDER = ["F", "D", "C", "B", "A"] as const;

const gradePalette: Record<string, string> = {
  A: cryoColors.gradeA,
  B: cryoColors.gradeB,
  C: cryoColors.gradeC,
  D: cryoColors.gradeD,
  F: cryoColors.gradeF
};

export const GradeDistributionMiniBar = ({
  distribution,
  tracked
}: {
  distribution: Array<{ grade: string; count: number }>;
  tracked?: number | null;
}) => {
  const counts = new Map<string, number>(GRADE_ORDER.map((grade) => [grade, 0]));
  for (const entry of distribution) {
    if (!counts.has(entry.grade) || !Number.isFinite(entry.count)) continue;
    counts.set(entry.grade, entry.count);
  }
  const graded = GRADE_ORDER.reduce((sum, grade) => sum + (counts.get(grade) ?? 0), 0);
  const trackedCount = tracked != null && !Number.isNaN(tracked) ? tracked : null;
  const notGraded = trackedCount == null ? null : Math.max(0, trackedCount - graded);
  const segments = [
    ...GRADE_ORDER.map((grade) => ({
      key: grade,
      label: grade,
      count: counts.get(grade) ?? 0,
      href: `/tables?grade=${grade}`,
      color: gradePalette[grade] ?? cryoColors.mutedSubtle
    })),
    {
      key: "not-graded",
      label: "Not graded",
      count: notGraded,
      href: "/health",
      color: cryoColors.mutedSubtle
    }
  ];
  const barTotal = graded + (notGraded ?? 0);

  return (
    <div className="mb-4" aria-label="Grade distribution">
      <p className="mb-2 text-xs text-muted-foreground [font-variant-numeric:tabular-nums]">
        {trackedCount == null
          ? `graded ${graded.toLocaleString()} of unknown`
          : `graded ${graded.toLocaleString()} of ${trackedCount.toLocaleString()}`}
      </p>
      <div className="flex h-2 overflow-hidden rounded-[var(--radius-sm)] bg-muted">
        {segments.map((segment) => {
          const count = segment.count ?? 0;
          if (!barTotal || count <= 0) return null;
          return (
            <div
              key={segment.key}
              className="h-full min-w-[2px]"
              style={{ width: `${(count / barTotal) * 100}%`, background: segment.color }}
              title={`${segment.label}: ${count.toLocaleString()}`}
            />
          );
        })}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {segments.map((segment) => (
          <Link
            key={segment.key}
            to={segment.href}
            className="inline-flex items-center gap-1.5 [font-variant-numeric:tabular-nums] hover:text-foreground"
          >
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: segment.color }} aria-hidden="true" />
            {segment.label} {segment.count == null ? "Unknown" : segment.count.toLocaleString()}
          </Link>
        ))}
      </div>
    </div>
  );
};
