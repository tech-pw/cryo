import type { TableStatsRead } from "../api/types";

export type ScoreFactorLevel = "ok" | "warn" | "bad";

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
export const buildHealthScoreFactors = (
  stats: StatsForScore | null | undefined,
  orphanBytes: number,
  storageBytes: number
): ScoreFactor[] => {
  const smallPct = stats?.total_files ? ((stats.small_files_count ?? 0) / stats.total_files) * 100 : 0;
  const skew = stats?.partition_skew_ratio ?? 0;
  const manifests = stats?.manifest_count ?? 0;
  const snapshots = stats?.total_snapshots ?? 0;
  const orphanPct = storageBytes > 0 ? (orphanBytes / storageBytes) * 100 : 0;

  return [
    {
      label: "Small-file pressure",
      detail: `${smallPct.toFixed(0)}% of files`,
      pct: Math.min(smallPct, 100),
      level: smallPct >= 30 ? "bad" : smallPct >= 10 ? "warn" : "ok"
    },
    {
      label: "Partition skew",
      detail: `${skew.toFixed(2)}x max/avg`,
      pct: Math.min((skew / 4) * 100, 100),
      level: skew >= 2 ? "bad" : skew >= 1.5 ? "warn" : "ok"
    },
    {
      label: "Manifest bloat",
      detail: `${manifests.toLocaleString()} manifests`,
      pct: Math.min((manifests / 150) * 100, 100),
      level: manifests >= 100 ? "bad" : manifests >= 30 ? "warn" : "ok"
    },
    {
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
  bad: "var(--gradeF, #b23a2e)"
};

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
