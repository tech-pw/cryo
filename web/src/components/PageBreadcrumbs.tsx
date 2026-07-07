import { Breadcrumb } from "antd";
import { Link, useLocation } from "react-router-dom";
import { useMemo } from "react";

export interface Crumb {
  label: string;
  to?: string;
}

const ROUTE_LABELS: Record<string, string> = {
  "/health": "Health",
  "/catalogs": "Catalogs",
  "/tables": "Tables",
  "/scans": "Scanner",
  "/ai": "Intelligence",
  "/governance": "Governance",
  "/queries": "Queries",
  "/cost": "Savings",
  "/cost-insights": "Cost insights",
  "/sla": "SLA",
  "/optimize": "Optimize",
  "/presets": "Presets",
  "/runs": "Runs",
  "/aws": "AWS",
  "/connections": "Connections",
  "/jobs": "Jobs",
  "/attribution": "Attribution",
  "/skillsets": "Skillsets"
};

const labelFor = (segment: string): string =>
  ROUTE_LABELS[`/${segment}`] ?? decodeURIComponent(segment);

export const PageBreadcrumbs = ({ root }: { root?: Crumb }) => {
  const { pathname } = useLocation();
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
      const label = labelFor(segment);
      crumbs.push({
        title: isLast ? <span aria-current="page">{label}</span> : <Link to={acc}>{label}</Link>
      });
    });
    return crumbs;
  }, [pathname, root]);

  if (items.length === 0) return null;
  return (
    <Breadcrumb
      aria-label="Breadcrumb"
      items={items}
      style={{ marginBottom: 12 }}
    />
  );
};