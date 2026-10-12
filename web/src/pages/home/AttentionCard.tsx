import { ArrowRight, CheckCheck, FileCheck2, Layers, TriangleAlert } from "lucide-react";
import { Link } from "react-router-dom";
import { Skeleton } from "../../components/ui/skeleton";
import { formatNumber } from "../../lib/format";
import { HomeCard } from "./HomeCard";

export const AttentionCard = ({ approvals, approvalsLoading, approvalsError, needsCompaction, failed, runHref, dashboardLoading, showScanner }: {
  approvals: number | null;
  approvalsLoading: boolean;
  approvalsError: boolean;
  needsCompaction: number | null;
  failed: number | null;
  runHref: string | null;
  dashboardLoading: boolean;
  showScanner: boolean;
}) => (
  <HomeCard title="Your next steps" description="Review the work that needs an operator." className="home-attention">
    <ul className="home-attention-list">
      {approvalsLoading ? <li><Skeleton className="my-4 h-12 w-full" /></li> : approvalsError || approvals == null ? <li className="py-3"><p className="home-caption">Approval status unavailable. <Link className="home-text-link" to="/decisions?tab=approvals#operation-approvals">Check decisions</Link></p></li> : <li><Link to="/decisions?tab=approvals#operation-approvals">
        <span className="home-attention-icon"><FileCheck2 size={17} aria-hidden="true" /></span><div><strong>{approvals > 0 ? `${formatNumber(approvals)} operation ${approvals === 1 ? "approval" : "approvals"} awaiting review` : "No pending operation approvals"}</strong><p>{approvals > 0 ? "Review proposed actions before they run." : "View maintenance decisions and their audit trail."}</p></div><ArrowRight size={16} aria-hidden="true" />
      </Link></li>}
      {dashboardLoading ? <li><Skeleton className="my-4 h-12 w-full" /></li> : needsCompaction != null ? <li><Link to="/tables?needs=compaction">
        <span className="home-attention-icon"><Layers size={17} aria-hidden="true" /></span><div><strong>{needsCompaction === 0 ? "No tables currently flagged" : `${formatNumber(needsCompaction)} ${needsCompaction === 1 ? "table needs" : "tables need"} compaction`}</strong><p>{needsCompaction === 0 ? "Nothing is waiting on a compaction plan." : "Inspect small files and choose a maintenance plan."}</p></div><ArrowRight size={16} aria-hidden="true" />
      </Link></li> : <li className="py-3"><p className="home-caption">Compaction need is not measured.</p></li>}
      {failed != null && failed > 0 && runHref ? <li><Link to={runHref}>
        <span className="home-attention-icon"><TriangleAlert size={17} aria-hidden="true" /></span><div><strong>{formatNumber(failed)} failed in the latest nightly run</strong><p>Read the failure details before retrying.</p></div><ArrowRight size={16} aria-hidden="true" />
      </Link></li> : null}
      {showScanner ? <li><Link to="/runs/scanner"><span className="home-attention-icon"><CheckCheck size={17} aria-hidden="true" /></span><div><strong>Keep the lake in view</strong><p>The scanner is off, a scan failed, or tables are waiting.</p></div><ArrowRight size={16} aria-hidden="true" /></Link></li> : null}
    </ul>
  </HomeCard>
);
