import { Activity, BarChart3, Cloud, DollarSign, LayoutDashboard, Plug, Zap, type LucideIcon } from "lucide-react";

import { DESTINATIONS, destinationHrefForPath, type Destination } from "../../lib/routes";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  adminOnly?: boolean;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

const DESTINATION_ICONS: Record<Destination["id"], LucideIcon> = {
  home: BarChart3,
  tables: LayoutDashboard,
  runs: Activity,
  decisions: Cloud,
  cost: DollarSign,
  insights: Zap,
  settings: Plug
};

const navItem = (id: Destination["id"]): NavItem => {
  const destination = DESTINATIONS.find((item) => item.id === id);
  if (!destination) throw new Error(`Missing destination ${id}`);
  return {
    href: destination.path,
    label: destination.label,
    icon: DESTINATION_ICONS[id]
  };
};

// Home and Settings stay pinned. The middle splits into the work operators do
// (tables, runs, decisions) and the questions they ask of it (cost, insights).
export const NAV_GROUPS: NavGroup[] = [
  { label: "", items: [navItem("home")] },
  { label: "Operate", items: [navItem("tables"), navItem("runs"), navItem("decisions")] },
  { label: "Understand", items: [navItem("cost"), navItem("insights")] },
  { label: "", items: [navItem("settings")] }
];

export const resolveActiveNavHref = (pathname: string): string => destinationHrefForPath(pathname);
