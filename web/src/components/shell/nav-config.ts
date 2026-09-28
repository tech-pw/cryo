import {
  Activity,
  BarChart3,
  Bot,
  Clock,
  Cloud,
  Cog,
  Copy,
  DollarSign,
  EyeOff,
  LayoutDashboard,
  Lightbulb,
  LineChart,
  Plug,
  RadioTower,
  ScanSearch,
  Shield,
  Zap,
  TriangleAlert,
  type LucideIcon
} from "lucide-react";

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

export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Overview",
    items: [
      { href: "/health", label: "Health", icon: BarChart3 },
      { href: "/impact", label: "Impact", icon: LineChart },
      { href: "/sla", label: "SLA", icon: Clock }
    ]
  },
  {
    label: "Tables",
    items: [{ href: "/catalogs", label: "Catalogs", icon: LayoutDashboard }]
  },
  {
    label: "Maintenance",
    items: [
      { href: "/operations", label: "Operations", icon: Activity },
      { href: "/smart-maintenance", label: "Recommendations", icon: Cloud },
      { href: "/control-room", label: "Control Room", icon: RadioTower }
    ]
  },
  {
    label: "Insights",
    items: [
      { href: "/cost", label: "Cost", icon: DollarSign },
      { href: "/queries", label: "Queries", icon: Zap },
      { href: "/anomalies", label: "Anomalies", icon: TriangleAlert },
      { href: "/scan-cost", label: "Scan Cost", icon: ScanSearch },
      { href: "/dark-tables", label: "Dark Tables", icon: EyeOff },
      { href: "/similarity", label: "Similarity", icon: Copy },
      { href: "/governance", label: "Governance", icon: Shield },
      { href: "/ai", label: "Intelligence", icon: Bot },
      { href: "/skillsets", label: "Skillsets", icon: Lightbulb, adminOnly: true }
    ]
  },
  {
    label: "Settings",
    items: [{ href: "/connections", label: "Connections", icon: Plug }]
  }
];

/** Map nested or legacy paths to a top-level nav href for active highlighting. */
const ACTIVE_PREFIXES: Array<{ prefix: string; navHref: string }> = [
  { prefix: "/tables", navHref: "/catalogs" },
  { prefix: "/operations", navHref: "/operations" },
  { prefix: "/scans", navHref: "/operations" },
  { prefix: "/runs", navHref: "/operations" },
  { prefix: "/presets", navHref: "/operations" },
  { prefix: "/jobs", navHref: "/operations" },
  { prefix: "/search", navHref: "/catalogs" },
  { prefix: "/impact", navHref: "/impact" }
];

export const resolveActiveNavHref = (pathname: string): string => {
  const alias = ACTIVE_PREFIXES.find(({ prefix }) => pathname === prefix || pathname.startsWith(`${prefix}/`));
  if (alias) return alias.navHref;

  const flat = NAV_GROUPS.flatMap((group) => group.items);
  const exact = flat.find((item) => pathname === item.href || pathname.startsWith(`${item.href}/`));
  return exact?.href ?? "/health";
};

export const BRAND_ICON = Cog;
