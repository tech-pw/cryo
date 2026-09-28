import type { TableStatsRead } from "../api/types";

export type ScoreFactorLevel = "ok" | "warn" | "bad" | "unknown";

export interface ScoreFactor {
  label: string;
  detail: string;
  pct: number;
  level: ScoreFactorLevel;
}

type StatsForScore = Pick<
  TableStatsRead,
  "small_files_count" | "total_files" | "partition_skew_ratio" | "manifest_count" | "total_snapshots"
>;

/** Pure frontend math over fields already fetched on the Table Detail page -- no new queries.
 * Thresholds are rules of thumb (not backend-computed signals) purely for this at-a-glance
 * breakdown; the real compaction/rebalance decisions still come from stats.needs_compaction
 * and friends elsewhere on the page. */
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

/** Shared color lookup so any other mini-bar visualization on this page (e.g. the Table
 * Metadata card's Partition skew tile) stays visually consistent with this breakdown. */
export const scoreFactorColor = (level: ScoreFactorLevel): string => LEVEL_COLOR[level];

/** Renders each factor reusing the page's existing .td-part-bar-wrap/.td-part-bar mini-bar
 * visual (already established by the Partitions table on this same page). */
export const HealthScoreBreakdown = ({ factors }: { factors: ScoreFactor[] }) => (
  <div className="td-score-breakdown">
    {factors.map((factor) => (
      <div className="td-score-breakdown-row" key={factor.label}>
        <span className="td-score-breakdown-label">{factor.label}</span>
        <span className="td-part-bar-wrap" style={{ width: 56 }}>
          <span
            className="td-part-bar"
            style={{ width: `${factor.pct}%`, background: LEVEL_COLOR[factor.level] }}
          />
        </span>
        <span className="td-score-breakdown-detail">{factor.detail}</span>
      </div>
    ))}
  </div>
);
