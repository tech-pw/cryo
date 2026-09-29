import type { TableStatsRead } from "../api/types";

export type ScoreFactorLevel = "ok" | "warn" | "bad" | "unknown";

export interface ScoreFactor {
  label: string;
  detail: string;
  pct: number;
  level: ScoreFactorLevel;
}

export interface PointDeduction {
  label: string;
  deduction: number;
  severity: "critical" | "warning" | string;
  reason: string;
}

type StatsForScore = Pick<
  TableStatsRead,
  "small_files_count" | "total_files" | "partition_skew_ratio" | "manifest_count" | "total_snapshots"
>;

const unknownFactor = (label: string, detail: string): ScoreFactor => ({
  label,
  detail,
  pct: 0,
  level: "unknown"
});

const isMeasured = (value: number | null | undefined): value is number =>
  value != null && Number.isFinite(value);

export const buildHealthScoreFactors = (
  stats: StatsForScore | null | undefined,
  orphanBytes: number | null | undefined,
  storageBytes: number | null | undefined
): ScoreFactor[] => {
  if (!stats) {
    return [
      unknownFactor("Small-file pressure", "Not scanned"),
      unknownFactor("Partition skew", "Not scanned"),
      unknownFactor("Manifest bloat", "Not scanned"),
      unknownFactor("Snapshot / orphan buildup", "Not scanned")
    ];
  }

  const totalFiles = stats.total_files;
  const smallFiles = stats.small_files_count;
  const smallKnown = isMeasured(totalFiles) && isMeasured(smallFiles);
  const smallPct = smallKnown ? (totalFiles > 0 ? (smallFiles / totalFiles) * 100 : 0) : null;
  const skew = isMeasured(stats.partition_skew_ratio) ? stats.partition_skew_ratio : null;
  const manifests = isMeasured(stats.manifest_count) ? stats.manifest_count : null;
  const snapshots = isMeasured(stats.total_snapshots) ? stats.total_snapshots : null;
  const orphanKnown = isMeasured(orphanBytes);
  const storageKnown = isMeasured(storageBytes);
  const orphanPct =
    !orphanKnown || !storageKnown ? null : storageBytes > 0 ? (orphanBytes / storageBytes) * 100 : 0;

  return [
    smallPct == null
      ? unknownFactor("Small-file pressure", "Not measured")
      : {
          label: "Small-file pressure",
          detail: `${smallPct.toFixed(0)}% of files`,
          pct: Math.min(smallPct, 100),
          level: smallPct >= 30 ? "bad" : smallPct >= 10 ? "warn" : "ok"
        },
    skew == null
      ? unknownFactor("Partition skew", "Not measured")
      : {
          label: "Partition skew",
          detail: `${skew.toFixed(2)}x max/avg`,
          pct: Math.min((skew / 4) * 100, 100),
          level: skew >= 2 ? "bad" : skew >= 1.5 ? "warn" : "ok"
        },
    manifests == null
      ? unknownFactor("Manifest bloat", "Not measured")
      : {
          label: "Manifest bloat",
          detail: `${manifests.toLocaleString()} manifests`,
          pct: Math.min((manifests / 150) * 100, 100),
          level: manifests >= 100 ? "bad" : manifests >= 30 ? "warn" : "ok"
        },
    snapshots == null
      ? unknownFactor("Snapshot / orphan buildup", "Not measured")
      : orphanPct == null
        ? snapshots >= 200
          ? {
              label: "Snapshot / orphan buildup",
              detail: `${snapshots.toLocaleString()} snapshots · orphan not measured`,
              pct: Math.min(snapshots / 5, 100),
              level: snapshots >= 500 ? "bad" : "warn"
            }
          : unknownFactor("Snapshot / orphan buildup", "Not measured")
        : {
            label: "Snapshot / orphan buildup",
            detail:
              orphanPct > 0
                ? `${orphanPct.toFixed(1)}% orphaned · ${snapshots.toLocaleString()} snapshots`
                : `${snapshots.toLocaleString()} snapshots`,
            pct: Math.min(Math.max(orphanPct * 4, snapshots / 5), 100),
            level: orphanPct >= 5 || snapshots >= 500 ? "bad" : orphanPct >= 1 || snapshots >= 200 ? "warn" : "ok"
          }
  ];
};

const LEVEL_COLOR: Record<ScoreFactorLevel, string> = {
  ok: "var(--gradeA, #1e8e5a)",
  warn: "var(--gradeC, #a9791e)",
  bad: "var(--gradeF, #b23a2e)",
  unknown: "var(--color-muted-foreground, #8b93a7)"
};

export const scoreFactorColor = (level: ScoreFactorLevel): string => LEVEL_COLOR[level];

const deductionPoints = (value: number): string =>
  Number.isFinite(value) ? `-${value} pts` : "Not measured";

export const HealthScoreBreakdown = ({
  factors,
  deductions
}: {
  factors: ScoreFactor[];
  deductions?: PointDeduction[] | null;
}) => {
  if (deductions) {
    return (
      <div className="td-score-breakdown">
        <p className="td-score-breakdown-label">Why this grade</p>
        {deductions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No point deductions recorded</p>
        ) : (
          <ul className="m-0 list-none space-y-1 p-0 text-sm">
            {deductions.map((item, index) => (
              <li key={`${item.label}-${index}`} className="flex items-baseline justify-between gap-3">
                <span>
                  <span className="font-medium">{item.label}</span>
                  {item.reason ? <span className="text-muted-foreground"> · {item.reason}</span> : null}
                  {item.severity ? <span className="text-muted-foreground"> · {item.severity}</span> : null}
                </span>
                <span className="shrink-0 tabular-nums">{deductionPoints(item.deduction)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  return (
    <div className="td-score-breakdown">
      <p className="td-score-breakdown-label">diagnostic, not score</p>
      <ul className="m-0 list-none space-y-1 p-0 text-sm">
        {factors.map((factor) => (
          <li key={factor.label} className="flex items-baseline justify-between gap-3">
            <span>{factor.label}</span>
            <span className="text-muted-foreground">{factor.detail}</span>
          </li>
        ))}
      </ul>
    </div>
  );
};
