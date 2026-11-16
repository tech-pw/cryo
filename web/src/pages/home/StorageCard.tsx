import { useId, useState } from "react";
import { AlertTriangle, ArrowUpRight, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";
import type { StorageOverview } from "../../api/storageOverview";
import { StorageTrendChart } from "../../components/charts/StorageTrendChart";
import { Badge } from "../../components/ui/badge";
import { ChartFrame } from "../../components/ui/chart-panel";
import { Skeleton } from "../../components/ui/skeleton";
import { formatCurrency, formatNumber, formatShortDate } from "../../lib/format";
import {
  SOURCE_LABELS,
  type SegmentKey,
  formatStorage,
  freeableBytes,
  heroView,
  listingCoverage,
  storageSegments,
  trendData
} from "../../lib/storageOverview";
import { HomeCard } from "./HomeCard";
import { cleanupProgress, storageTrendDelta, waffleCells } from "./storageStory";
import { useCountUp } from "./useCountUp";

const Kind = ({ measured }: { measured: boolean }) => (
  <Badge variant={measured ? "success" : "outline"} className="home-storage-kind">{measured ? "Measured" : "Estimate"}</Badge>
);

const CountUpBytes = ({ bytes }: { bytes: number }) => {
  const value = useCountUp(bytes);
  return <>{formatStorage(value == null ? bytes : Math.max(value, bytes / 100))}</>;
};

const RING_R = 70;
const RING_C = 2 * Math.PI * RING_R;

const ReclaimRing = ({ data }: { data: StorageOverview }) => {
  const gradient = useId();
  const cumulative = data.cumulative;
  const recorded = cumulative.days_recorded > 0 && cumulative.since != null;
  const progress = cleanupProgress(recorded ? cumulative.bytes_freed : 0, data.breakdown);
  const pct = data.breakdown.tables_with_cost > 0 ? progress.pct : null;
  const arc = pct == null ? 0 : Math.max(pct, progress.reclaimed > 0 ? 1.5 : 0);
  return (
    <div
      className="home-ring"
      role={pct == null ? undefined : "progressbar"}
      aria-valuemin={pct == null ? undefined : 0}
      aria-valuemax={pct == null ? undefined : 100}
      aria-valuenow={pct ?? undefined}
      aria-label={pct == null ? undefined : `${formatStorage(progress.reclaimed)} reclaimed, ${formatStorage(progress.remaining)} still freeable`}
    >
      <svg viewBox="0 0 180 180" aria-hidden="true">
        <defs>
          <linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="var(--color-savings)" stopOpacity={0.55} />
            <stop offset="100%" stopColor="var(--color-savings)" />
          </linearGradient>
        </defs>
        <circle className="home-ring-rest" cx="90" cy="90" r={RING_R} />
        <circle
          className="home-ring-arc"
          cx="90"
          cy="90"
          r={RING_R}
          stroke={`url(#${gradient})`}
          strokeDasharray={`${(arc / 100) * RING_C} ${RING_C}`}
          transform="rotate(-90 90 90)"
        />
      </svg>
      <div className="home-ring-center">
        {recorded ? (
          <>
            <strong><CountUpBytes bytes={cumulative.bytes_freed} /></strong>
            <span>freed so far</span>
            {pct != null ? <span className="home-ring-pct">{pct}% reclaimed</span> : null}
          </>
        ) : (
          <span>Total starts with the first daily report</span>
        )}
      </div>
    </div>
  );
};

const Hero = ({ data }: { data: StorageOverview }) => {
  const freed = data.freed_24h;
  const view = heroView(freed);
  const sources = Object.entries(freed.bytes_by_source ?? {}).filter(([, bytes]) => bytes > 0);
  const cumulative = data.cumulative;
  const progress = cleanupProgress(cumulative.days_recorded > 0 ? cumulative.bytes_freed : 0, data.breakdown);
  return (
    <section className="home-storage-hero" aria-label="Last 24 hours">
      <ReclaimRing data={data} />
      <div className="home-storage-hero-copy">
        <div className="home-storage-eyebrow"><span className="home-caption">Last 24 hours</span>{view.state === "measured" ? <Kind measured /> : null}</div>
        {view.state === "idle" ? (
          <>
            <p className="home-storage-headline">No cleanup ran in the last 24 hours</p>
            <p className="home-caption">Nightly maintenance expires old snapshots and removes orphan files. Freed bytes show here after the next run. <Link className="home-text-link" to="/runs">Check the schedule</Link></p>
          </>
        ) : (
          <>
            <p className="home-storage-headline">
              {view.state === "measured" ? <>Cryo freed <strong className="home-storage-pop"><CountUpBytes bytes={freed.bytes_freed} /></strong></> : <>Cryo cleaned <strong>{formatNumber(freed.tables_cleaned)}</strong> {freed.tables_cleaned === 1 ? "table" : "tables"}</>}
            </p>
            <p className="home-storage-chips">
              {view.state === "measured" ? <span className="home-storage-chip">{formatNumber(freed.tables_cleaned)} {freed.tables_cleaned === 1 ? "table" : "tables"} cleaned</span> : null}
              {view.state === "measured" && freed.savings_usd_month != null && freed.savings_usd_month > 0 ? <span className="home-storage-chip is-money">≈ {formatCurrency(freed.savings_usd_month)}/month saved</span> : null}
            </p>
            <p className="home-caption">
              {view.state === "measured"
                ? `Measured on ${view.measuredPct}% of actions (${formatNumber(freed.actions_measured)} of ${formatNumber(freed.actions_succeeded)}). The rest freed space too, but did not report a byte count. Savings are estimated at list price.`
                : `${formatNumber(freed.actions_succeeded)} cleanup actions succeeded, but none reported a byte count. Cryo counts table files before and after each action; engines that skip that step still clean, they just are not counted here.`}
            </p>
            {sources.length ? <p className="home-caption">{sources.map(([key, bytes]) => `${formatStorage(bytes)} ${SOURCE_LABELS[key] ?? key}`).join(" · ")}</p> : null}
          </>
        )}
        <p className="home-caption home-storage-since">
          {cumulative.days_recorded > 0 && cumulative.since ? (
            <>
              <strong>{formatStorage(cumulative.bytes_freed)}</strong> freed since {formatShortDate(cumulative.since)}
              {progress.remaining > 0 ? <>, <strong>{formatStorage(progress.remaining)}</strong> still to go</> : null}
              {" "}· measured days only{cumulative.days_unmeasured ? `, ${cumulative.days_unmeasured} unmeasured ${cumulative.days_unmeasured === 1 ? "day" : "days"} not counted` : ""}
            </>
          ) : (
            "The running total starts with the first daily report."
          )}
        </p>
      </div>
    </section>
  );
};

const Breakdown = ({ data }: { data: StorageOverview }) => {
  const [focus, setFocus] = useState<SegmentKey | null>(null);
  const b = data.breakdown;
  const segments = storageSegments(b);
  const coverage = listingCoverage(b);
  if (b.tables_with_cost === 0) {
    return (
      <section className="home-storage-breakdown" aria-label="Where storage goes">
        <p className="home-caption">Where storage goes</p>
        <p className="home-storage-teach">No table has cost data yet. The scanner fills this in on its next pass; each table needs one completed scan.</p>
      </section>
    );
  }
  const cells = waffleCells(segments);
  const meta = new Map(segments.map((s) => [s.key, s]));
  const after = Math.max(0, b.total_bytes - b.dead_bytes - b.orphan_bytes - b.compaction_savings_bytes);
  const drop = b.total_bytes > 0 ? Math.round(((b.total_bytes - after) / b.total_bytes) * 100) : 0;
  return (
    <section className="home-storage-breakdown" aria-label="Where storage goes">
      <div className="home-storage-eyebrow">
        <span className="home-caption">Where your <strong className="home-storage-total">{formatStorage(b.total_bytes)}</strong> lives · each square ≈ {formatStorage(b.total_bytes / 100)}</span>
        <Kind measured={false} />
      </div>
      <div className="home-storage-waffle-wrap">
        <div
          className={`home-waffle${focus ? " has-focus" : ""}`}
          role="img"
          aria-label={segments.map((s) => `${s.label} ${formatStorage(s.bytes)}`).join(", ")}
        >
          {cells.map((key, i) => (
            <span
              key={i}
              className={`home-waffle-cell home-seg-${key}${meta.get(key)?.freeable ? " is-freeable" : ""}${focus === key ? " is-focus" : ""}`}
              style={{ animationDelay: `${i * 6}ms` }}
              onMouseEnter={() => setFocus(key)}
              onMouseLeave={() => setFocus(null)}
              title={`${meta.get(key)?.label}: ${formatStorage(meta.get(key)?.bytes)}`}
            />
          ))}
        </div>
        <ul className="home-storage-legend">
          {segments.map((s) => (
            <li
              key={s.key}
              title={s.hint}
              tabIndex={0}
              className={focus === s.key ? "is-focus" : undefined}
              onMouseEnter={() => setFocus(s.key)}
              onMouseLeave={() => setFocus(null)}
              onFocus={() => setFocus(s.key)}
              onBlur={() => setFocus(null)}
            >
              <span className={`home-storage-swatch home-seg-${s.key}${s.freeable ? " is-freeable" : ""}`} aria-hidden="true" />
              <span className="home-storage-legend-label">{s.label}{s.freeable ? <span className="home-storage-freeable">freeable</span> : null}</span>
              <span className="home-storage-legend-value">{formatStorage(s.bytes)}</span>
              <span className="home-storage-legend-pct">{s.pct.toFixed(s.pct > 0 && s.pct < 1 ? 1 : 0)}%</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="home-storage-whatif">
        <Sparkles size={15} aria-hidden="true" />
        <span>
          <strong>{formatStorage(freeableBytes(b))}</strong> is freeable by snapshot expiry and orphan cleanup
          {b.compaction_savings_bytes > 0 ? <>, and compaction could save about <strong>{formatStorage(b.compaction_savings_bytes)}</strong> more</> : null}.
          {drop > 0 ? <> Cleaned up, the lake would weigh <strong>{formatStorage(after)}</strong> ({drop}% lighter).</> : null}
        </span>
      </div>
      <p className="home-caption">
        Storage listing for {formatNumber(b.tables_with_listing)} of {formatNumber(b.tables_total)} tables.{" "}
        {coverage === "none"
          ? "Orphans and retained history need an S3 listing, so they read 0 until the cost scan lists table storage."
          : coverage === "partial"
            ? "Orphans and retained history count only listed tables, so the real totals are higher."
            : null}
      </p>
    </section>
  );
};

const Trend = ({ data }: { data: StorageOverview }) => {
  const rows = trendData(data.trend);
  const hasLive = rows.some((r) => r.live != null);
  const empty = rows.filter((r) => r.total != null).length < 2;
  const delta = storageTrendDelta(data.trend);
  return (
    <section className="home-storage-trend" aria-label="Storage over 60 days">
      <div className="home-storage-eyebrow">
        <span className="home-caption">{hasLive ? "The waste gap: total storage against live data, last 60 days" : "Last 60 days"}</span>
        {delta && delta.bytes !== 0 ? (
          <span className={`home-storage-delta ${delta.bytes < 0 ? "is-down" : "is-up"}`}>
            Total storage {delta.bytes < 0 ? "down" : "up"} {formatStorage(Math.abs(delta.bytes))} ({Math.abs(delta.pct)}%) in {delta.days} days
          </span>
        ) : null}
      </div>
      <ChartFrame
        size="sm"
        empty={empty}
        emptyTitle="The trend is still recording"
        emptyDescription="The daily report saves one point per day. The line appears after two reports."
        ariaLabel="Total storage, live data, the gap between them, and cumulative bytes freed by Cryo over the last 60 days"
      >
        <StorageTrendChart rows={rows} hasLive={hasLive} />
      </ChartFrame>
      {!empty ? (
        <ul className="home-storage-trend-key" aria-label="Chart legend">
          <li><span className="home-storage-line home-line-total" />Total storage</li>
          {hasLive ? <li><span className="home-storage-line home-line-live" />Live data</li> : null}
          {hasLive ? <li><span className="home-storage-band" />Gap: history, dead files and orphans</li> : null}
          <li><span className="home-storage-line home-line-freed" />Freed by Cryo, cumulative (measured)</li>
        </ul>
      ) : null}
      {!empty && !hasLive ? <p className="home-caption">Live data is recorded from today on; earlier days only kept the total.</p> : null}
    </section>
  );
};

export const StorageCard = ({ data, loading, error }: { data: StorageOverview | undefined; loading: boolean; error: boolean }) => {
  const findings = data?.versioning_findings ?? null;
  return (
    <HomeCard title="Storage impact" description="What Cryo freed, where the bytes are, and how storage is trending." className="home-storage" busy={loading}>
      {findings && findings.length ? (
        <div className="home-storage-warning" role="note">
          <AlertTriangle size={15} aria-hidden="true" />
          <span>
            <strong>{findings.length === 1 ? "1 bucket keeps" : `${findings.length} buckets keep`} deleted versions.</strong>{" "}
            Versioning is on without a noncurrent-version expiration rule, so files Cryo deletes stay billed as old versions. Add a lifecycle rule to {findings.slice(0, 3).map((f) => f.bucket).join(", ")}{findings.length > 3 ? " and others" : ""}.
          </span>
        </div>
      ) : null}
      {loading ? (
        <div className="home-storage-layout"><Skeleton className="h-32 w-full" /><Skeleton className="h-32 w-full" /></div>
      ) : error || !data ? (
        <p className="home-storage-teach">Storage figures are unavailable right now. Other cards still work; this one retries every few minutes.</p>
      ) : (
        <>
          <div className="home-storage-layout">
            <Hero data={data} />
            <Breakdown data={data} />
          </div>
          <Trend data={data} />
        </>
      )}
      <div className="home-card-footer"><span className="home-caption">Measured figures come from maintenance results. Estimates come from scan data.</span><Link className="home-text-link" to="/cost?tab=impact">Explore cost <ArrowUpRight size={14} aria-hidden="true" /></Link></div>
    </HomeCard>
  );
};
