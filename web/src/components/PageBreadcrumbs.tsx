import { Link, useLocation } from "react-router-dom";
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { listPresets } from "../api/client";
import { useAuth } from "../lib/auth";
import { shortRunId } from "../lib/operations";
import { Breadcrumb } from "./ui/breadcrumb";

export interface Crumb {
  label: string;
  to?: string;
}

const ROUTE_LABELS: Record<string, string> = {
  "/health": "Health",
  "/catalogs": "Catalogs",
  "/tables": "Tables",
  "/operations": "Operations",
  "/operations/scanner": "Scanner",
  "/ai": "Intelligence",
  "/governance": "Governance",
  "/queries": "Queries",
  "/cost": "Cost",
  "/sla": "SLA",
  "/smart-maintenance": "Smart Maintenance",
  "/connections": "Connections",
  "/jobs": "Jobs",
  "/search": "Search",
  "/attribution": "Attribution",
  "/skillsets": "Skillsets",
  "/scans": "Scanner",
  "/presets": "Operations",
  "/runs": "Operations"
};

const labelFor = (segment: string, acc: string): string => {
  if (ROUTE_LABELS[acc]) return ROUTE_LABELS[acc];
  if (acc.startsWith("/operations/runs/")) return `Run ${shortRunId(segment)}`;
  if (acc.startsWith("/operations/presets/")) return segment;
  if (acc.startsWith("/operations/jobs/") || acc.startsWith("/jobs/")) return `Job ${shortRunId(segment)}`;
  return decodeURIComponent(segment);
};

export const PageBreadcrumbs = ({ root }: { root?: Crumb }) => {
  const { pathname } = useLocation();
  const { api, token } = useAuth();
  const presetIdMatch = pathname.match(/^\/operations\/presets\/([^/]+)/);
  const presetId = presetIdMatch?.[1];
  const presets = useQuery({
    queryKey: ["presets", token],
    queryFn: () => listPresets(api),
    enabled: Boolean(presetId),
    staleTime: 60_000
  });
  const presetName = presets.data?.find((item) => item.id === presetId)?.name;

  const items = useMemo(() => {
    const parts = pathname.split("/").filter(Boolean);
    const crumbs: { title: React.ReactNode }[] = [];
    if (root) {
      crumbs.push({
        title: root.to ? <Link to={root.to}>{root.label}</Link> : root.label
      });
    }
    let acc = "";
    parts.forEach((segment, idx) => {
      acc += `/${segment}`;
      const isLast = idx === parts.length - 1;
      let label = labelFor(segment, acc);
      if (presetId && acc === `/operations/presets/${presetId}` && presetName) {
        label = presetName;
      }
      crumbs.push({
        title: isLast ? <span aria-current="page">{label}</span> : <Link to={acc}>{label}</Link>
      });
    });
    return crumbs;
  }, [pathname, presetId, presetName, root]);

  if (items.length === 0) return null;
  return <Breadcrumb items={items} />;
};
