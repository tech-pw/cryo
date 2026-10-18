import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";
import { getTableChecklist, type ChecklistItemRead } from "../../../api/client";
import { useAuth } from "../../../lib/auth";
import { formatTime } from "../../../lib/format";
import { QueryErrorAlert } from "../../common";
import { Badge, type BadgeProps } from "../../ui/badge";
import { Collapsible } from "../../ui/collapsible";
import { Skeleton } from "../../ui/skeleton";
import { OverviewCard } from "./OverviewCard";

const VERDICT: Record<ChecklistItemRead["status"], { label: string; variant: BadgeProps["variant"] }> = {
  red: { label: "Fix", variant: "danger" },
  yellow: { label: "Improve", variant: "warning" },
  unknown: { label: "Unknown", variant: "secondary" },
  green: { label: "Passing", variant: "success" }
};
const ORDER = { red: 0, yellow: 1, unknown: 2, green: 3 };

const fixFor = (key: string, tableId: string): { href: string; label: string } | null => {
  const base = `/tables/${tableId}`;
  if (key === "distribution_mode" || key === "sort_order") return { href: `${base}?tab=config#config-properties`, label: "Change in Config" };
  if (key === "partition_match") return { href: `${base}?tab=files#files-layout`, label: "See layout" };
  if (key === "storage_class") return { href: `${base}?tab=cost#archive`, label: "See storage classes" };
  if (["file_size", "delete_files", "manifest_bloat", "stats_current", "cadence_match", "snapshot_retention", "orphan_cleanup"].includes(key)) {
    return { href: `${base}?tab=maintenance`, label: "Maintain" };
  }
  return null;
};

const CheckRow = ({ item, tableId }: { item: ChecklistItemRead; tableId: string }) => {
  const fix = item.status === "red" || item.status === "yellow" ? fixFor(item.key, tableId) : null;
  return (
    <li className="td-check" data-status={item.status}>
      <Badge variant={VERDICT[item.status].variant}>{VERDICT[item.status].label}</Badge>
      <div className="td-check-text">
        <b>{item.label}</b>
        <p>{item.reason}</p>
      </div>
      {fix ? (
        <Link className="td-overview-link" to={fix.href}>
          {fix.label} <ArrowUpRight size={14} aria-hidden="true" />
        </Link>
      ) : null}
    </li>
  );
};

/** Layout checks shown as reasons and fixes, without their own weighted score beside the grade. */
export const OptimizationChecks = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const checklist = useQuery({
    queryKey: ["table-checklist", token, tableId],
    queryFn: () => getTableChecklist(api, tableId),
    enabled: Boolean(tableId),
    retry: false
  });
  const items = [...(checklist.data?.items ?? [])].sort(
    (a, b) => ORDER[a.status] - ORDER[b.status] || b.weight_pct - a.weight_pct
  );
  const open = items.filter((item) => item.status === "red" || item.status === "yellow");
  const rest = items.filter((item) => item.status === "green" || item.status === "unknown");
  const counts = checklist.data?.summary.counts;

  return (
    <OverviewCard
      id="overview-checks"
      title="Layout checks"
      description="File size, deletes, manifests, sort and distribution: what to change, and where."
      className="td-ov-checks"
    >
      <QueryErrorAlert error={checklist.error} title="Couldn't load the layout checks" />
      {checklist.isPending ? <Skeleton className="h-24 w-full" /> : null}
      {counts ? (
        <p className="td-checks-tally">
          <span data-status="red">{counts.red + counts.yellow} to act on</span>
          <span data-status="green">{counts.green} passing</span>
          {counts.unknown ? <span data-status="unknown">{counts.unknown} need more evidence</span> : null}
        </p>
      ) : null}
      {open.length ? (
        <ul className="td-checks">
          {open.map((item) => (
            <CheckRow key={item.key} item={item} tableId={tableId} />
          ))}
        </ul>
      ) : checklist.data ? (
        <p className="td-overview-caption">Nothing to act on. Every check with evidence is passing.</p>
      ) : null}
      {rest.length ? (
        <Collapsible key={tableId} label={`Show ${rest.length} passing or unknown`}>
          <ul className="td-checks">
            {rest.map((item) => (
              <CheckRow key={item.key} item={item} tableId={tableId} />
            ))}
          </ul>
        </Collapsible>
      ) : null}
      {checklist.data ? <p className="td-overview-caption">Checked {formatTime(checklist.data.checked_at)}.</p> : null}
    </OverviewCard>
  );
};
