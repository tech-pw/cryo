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
  distribution
}: {
  distribution: Array<{ grade: string; count: number }>;
}) => {
  const total = distribution.reduce((sum, entry) => sum + entry.count, 0);
  if (!total) return null;

  return (
    <div className="mb-4" aria-label="Grade distribution">
      <div className="flex h-2 overflow-hidden rounded-[var(--radius-sm)] bg-muted">
        {GRADE_ORDER.map((grade) => {
          const count = distribution.find((entry) => entry.grade === grade)?.count ?? 0;
          if (!count) return null;
          const widthPct = (count / total) * 100;
          return (
            <div
              key={grade}
              className="h-full min-w-[2px]"
              style={{ width: `${widthPct}%`, background: gradePalette[grade] ?? cryoColors.mutedSubtle }}
              title={`${grade}: ${count.toLocaleString()}`}
            />
          );
        })}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {GRADE_ORDER.map((grade) => {
          const count = distribution.find((entry) => entry.grade === grade)?.count ?? 0;
          if (!count) return null;
          return (
            <span key={grade} className="inline-flex items-center gap-1.5 [font-variant-numeric:tabular-nums]">
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ background: gradePalette[grade] ?? cryoColors.mutedSubtle }}
              />
              {grade} {count.toLocaleString()}
            </span>
          );
        })}
      </div>
    </div>
  );
};
