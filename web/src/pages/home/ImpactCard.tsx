import { ArrowUpRight, HardDriveDownload } from "lucide-react";
import { Link } from "react-router-dom";
import type { ReclaimDisplay } from "../../lib/nowStrip";
import { formatBytesGb, formatCurrency } from "../../lib/format";
import { Skeleton } from "../../components/ui/skeleton";
import { HomeCard } from "./HomeCard";

const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);

export const ImpactCard = ({ reclaim, reclaimableBytes, loading, error, wowPct = null, projectedSavingsUsd = null }: {
  reclaim: ReclaimDisplay;
  reclaimableBytes: number | null;
  loading: boolean;
  error: boolean;
  wowPct?: number | null;
  projectedSavingsUsd?: number | null;
}) => (
  <HomeCard title="Storage impact" description="Measured results and the next opportunity." className="home-impact">
    <div className="home-impact-result">
      <span className="home-impact-icon"><HardDriveDownload size={19} aria-hidden="true" /></span>
      <div><p className="home-caption">Reclaimed yesterday</p><p className={reclaim.tone === "number" ? "home-impact-value" : "home-impact-missing"}>{reclaim.hero}{reclaim.unit ? <span> {reclaim.unit}</span> : null}</p></div>
    </div>
    <p className="home-caption home-impact-evidence">
      {reclaim.caption ?? (reclaim.tone === "number" ? "Based on recorded maintenance results." : "Reported when maintenance records byte evidence.")}
      {reclaim.tone === "number" && finite(wowPct) ? <span className="home-impact-wow"> {wowPct >= 0 ? "+" : "−"}{Math.abs(wowPct).toFixed(0)}% vs the same day last week.</span> : null}
    </p>
    <div className="home-opportunity">
      <div>
        <p className="home-caption">Reclaimable now</p>
        {loading ? <Skeleton className="mt-2 h-6 w-28" /> : <p className="home-opportunity-value">{error ? "Unavailable" : reclaimableBytes == null || !Number.isFinite(reclaimableBytes) ? "Not measured" : formatBytesGb(reclaimableBytes)}</p>}
        {!loading && !error && finite(projectedSavingsUsd) && projectedSavingsUsd > 0 ? <p className="home-caption">≈ {formatCurrency(projectedSavingsUsd)}/month in S3 storage</p> : null}
      </div>
      <span className="home-estimate-label">Estimate</span>
    </div>
    <div className="home-card-footer"><span className="home-caption">Potential cleanup, subject to retention policies.</span><Link className="home-text-link" to="/cost?tab=impact">Explore cost <ArrowUpRight size={14} aria-hidden="true" /></Link></div>
  </HomeCard>
);
