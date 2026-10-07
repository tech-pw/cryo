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

export const NAV_GROUPS: NavGroup[] = [
  {
    label: "",
    items: DESTINATIONS.map((destination) => ({
      href: destination.path,
      label: destination.label,
      icon: DESTINATION_ICONS[destination.id]
    }))
  }
];

export const resolveActiveNavHref = (pathname: string): string => destinationHrefForPath(pathname);
