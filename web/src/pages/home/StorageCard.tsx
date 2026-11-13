import { AlertTriangle, ArrowUpRight, HardDriveDownload } from "lucide-react";
import { Link } from "react-router-dom";
import type { StorageOverview } from "../../api/storageOverview";
import { StorageTrendChart } from "../../components/charts/StorageTrendChart";
import { Badge } from "../../components/ui/badge";
import { ChartFrame } from "../../components/ui/chart-panel";
import { Skeleton } from "../../components/ui/skeleton";
import { formatCurrency, formatNumber, formatShortDate } from "../../lib/format";
import {
  SOURCE_LABELS,
  formatStorage,
  formatTb,
  freeableBytes,
  heroView,
  listingCoverage,
  storageSegments,
  trendData
} from "../../lib/storageOverview";
import { HomeCard } from "./HomeCard";

const Kind = ({ measured }: { measured: boolean }) => (
  <Badge variant={measured ? "success" : "outline"} className="home-storage-kind">{measured ? "Measured" : "Estimate"}</Badge>
);

const Hero = ({ data }: { data: StorageOverview }) => {
  const freed = data.freed_24h;
  const view = heroView(freed);
  const sources = Object.entries(freed.bytes_by_source ?? {}).filter(([, bytes]) => bytes > 0);
  const cumulative = data.cumulative;
  return (
    <section className="home-storage-hero" aria-label="Last 24 hours">
      <div className="home-storage-eyebrow"><span className="home-caption">Last 24 hours</span><Kind measured={view.state === "measured"} /></div>
      {view.state === "idle" ? (
        <>
          <p className="home-storage-headline">No cleanup ran in the last 24 hours</p>
          <p className="home-caption">Nightly maintenance expires old snapshots and removes orphan files. Freed bytes show here after the next run. <Link className="home-text-link" to="/runs">Check the schedule</Link></p>
        </>
      ) : (
        <>
          <p className="home-storage-headline">
            {view.state === "measured" ? <>Cryo freed <strong>{formatStorage(freed.bytes_freed)}</strong></> : <>Cryo cleaned <strong>{formatNumber(freed.tables_cleaned)}</strong> {freed.tables_cleaned === 1 ? "table" : "tables"}</>}
          </p>
          <p className="home-storage-facts">
            {view.state === "measured" ? <span>{formatNumber(freed.tables_cleaned)} {freed.tables_cleaned === 1 ? "table" : "tables"} cleaned</span> : null}
            {view.state === "measured" && freed.savings_usd_month != null && freed.savings_usd_month > 0 ? <span>≈ {formatCurrency(freed.savings_usd_month)}/month saved <span className="home-caption">(est. at list price)</span></span> : null}
          </p>
          <p className="home-caption">
            {view.state === "measured"
              ? `Measured on ${view.measuredPct}% of actions (${formatNumber(freed.actions_measured)} of ${formatNumber(freed.actions_succeeded)}). The rest freed space too, but did not report a byte count.`
              : `${formatNumber(freed.actions_succeeded)} cleanup actions succeeded, but none reported a byte count. Cryo counts table files before and after each action; engines that skip that step still clean, they just are not counted here.`}
          </p>
          {sources.length ? <p className="home-caption">{sources.map(([key, bytes]) => `${formatStorage(bytes)} ${SOURCE_LABELS[key] ?? key}`).join(" · ")}</p> : null}
        </>
      )}
      <div className="home-storage-cumulative">
        <HardDriveDownload size={16} aria-hidden="true" />
        {cumulative.days_recorded > 0 && cumulative.since ? (
          <span><strong>{formatStorage(cumulative.bytes_freed)}</strong> freed since {formatShortDate(cumulative.since)}<span className="home-caption"> · measured days only{cumulative.days_unmeasured ? `, ${cumulative.days_unmeasured} unmeasured ${cumulative.days_unmeasured === 1 ? "day" : "days"} not counted` : ""}</span></span>
        ) : (
          <span className="home-caption">The running total starts with the first daily report.</span>
        )}
      </div>
    </section>
  );
};

const Breakdown = ({ data }: { data: StorageOverview }) => {
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
  return (
    <section className="home-storage-breakdown" aria-label="Where storage goes">
      <div className="home-storage-eyebrow">
        <span className="home-caption">Where storage goes · <strong className="home-storage-total">{formatStorage(b.total_bytes)}</strong></span>
        <Kind measured={false} />
      </div>
      <div className="home-storage-bar" role="img" aria-label={segments.map((s) => `${s.label} ${formatStorage(s.bytes)}`).join(", ")}>
        {segments.filter((s) => s.pct > 0).map((s) => (
          <span key={s.key} className={`home-storage-seg home-seg-${s.key}`} style={{ flexGrow: s.pct }} title={`${s.label}: ${formatStorage(s.bytes)} (${s.pct.toFixed(1)}%). ${s.hint}`} />
        ))}
      </div>
      <ul className="home-storage-legend">
        {segments.map((s) => (
          <li key={s.key} title={s.hint}>
            <span className={`home-storage-swatch home-seg-${s.key}`} aria-hidden="true" />
            <span className="home-storage-legend-label">{s.label}{s.freeable ? <span className="home-storage-freeable">freeable</span> : null}</span>
            <span className="home-storage-legend-value">{formatStorage(s.bytes)}</span>
            <span className="home-storage-legend-pct">{s.pct.toFixed(s.pct > 0 && s.pct < 1 ? 1 : 0)}%</span>
          </li>
        ))}
      </ul>
      <p className="home-caption home-storage-freeable-line">
        <strong>{formatStorage(freeableBytes(b))}</strong> is freeable by snapshot expiry and orphan cleanup.
        {b.compaction_savings_bytes > 0 ? <> Compaction could save about <strong>{formatStorage(b.compaction_savings_bytes)}</strong> more (estimate).</> : null}
      </p>
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
  return (
    <section className="home-storage-trend" aria-label="Storage over 60 days">
      <div className="home-storage-eyebrow"><span className="home-caption">Last 60 days</span></div>
      <ChartFrame
        size="sm"
        empty={empty}
        emptyTitle="The trend is still recording"
        emptyDescription="The daily report saves one point per day. The line appears after two reports."
        ariaLabel="Total storage, live data and cumulative bytes freed by Cryo over the last 60 days"
      >
        <StorageTrendChart rows={rows} hasLive={hasLive} />
      </ChartFrame>
      {!empty ? (
        <ul className="home-storage-trend-key" aria-label="Chart legend">
          <li><span className="home-storage-line home-line-total" />Total storage</li>
          {hasLive ? <li><span className="home-storage-line home-line-live" />Live data</li> : null}
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
