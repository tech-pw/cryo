export interface Destination {
  id: "home" | "tables" | "runs" | "decisions" | "cost" | "insights" | "settings";
  label: string;
  path: string;
  subtitle: string;
}

export const DESTINATIONS: readonly Destination[] = [
  {
    id: "home",
    label: "Home",
    path: "/",
    subtitle: "What happened last night, and is the lake worse?"
  },
  {
    id: "tables",
    label: "Tables",
    path: "/tables",
    subtitle: "Which tables, and what is wrong with this one?"
  },
  {
    id: "runs",
    label: "Runs",
    path: "/runs",
    subtitle: "What ran, what failed, and what is held?"
  },
  {
    id: "decisions",
    label: "Decisions",
    path: "/decisions",
    subtitle: "What is waiting on a person, and why was a table skipped?"
  },
  {
    id: "cost",
    label: "Cost",
    path: "/cost",
    subtitle: "What do we pay, what did maintenance change, and who is reading?"
  },
  {
    id: "insights",
    label: "Insights",
    path: "/insights",
    subtitle: "Which tables are the same physical data, and which queries are slow?"
  },
  {
    id: "settings",
    label: "Settings",
    path: "/settings",
    subtitle: "What is connected, and who may call the model?"
  }
];

export interface SettingsSection {
  id: string;
  label: string;
  path: string;
  adminOnly?: boolean;
}

export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
  { id: "connections", label: "Connections", path: "/settings/connections" },
  { id: "ai", label: "AI & models", path: "/settings/ai" },
  { id: "notifications", label: "Notifications", path: "/settings/notifications" },
  { id: "access", label: "Access & policy", path: "/settings/access" },
  { id: "admin", label: "Admin", path: "/settings/admin", adminOnly: true }
];

export interface PaletteEntry {
  label: string;
  href: string;
  keywords: string;
  group: string;
  adminOnly?: boolean;
}

const LEGACY_PALETTE_ALIASES: readonly PaletteEntry[] = [
  {
    label: "Dark tables",
    href: "/tables?view=unused",
    keywords: "dark tables unused",
    group: "Tables"
  },
  {
    label: "Control Room",
    href: "/runs?tab=holds",
    keywords: "control room holds",
    group: "Runs"
  },
  {
    label: "Scan cost",
    href: "/cost?tab=reads",
    keywords: "scan cost reads",
    group: "Cost"
  },
  {
    label: "SLA",
    href: "/tables?view=freshness",
    keywords: "sla freshness",
    group: "Tables"
  },
  {
    label: "Smart maintenance",
    href: "/decisions?tab=recommendations",
    keywords: "smart maintenance recommendations optimize",
    group: "Decisions"
  },
  {
    label: "Skillsets",
    href: "/settings/ai?tab=skillsets",
    keywords: "skillsets admin",
    group: "Settings",
    adminOnly: true
  }
];

export const commandPaletteEntries = (role: string): PaletteEntry[] => {
  const destinations = DESTINATIONS.map((destination) => ({
    label: destination.label,
    href: destination.path,
    keywords: `${destination.label} ${destination.subtitle}`,
    group: destination.label
  }));
  const aliases = LEGACY_PALETTE_ALIASES.filter((entry) => !entry.adminOnly || role === "admin");
  return [...destinations, ...aliases];
};

export const visibleSettingsSections = (role: string): SettingsSection[] =>
  SETTINGS_SECTIONS.filter((section) => !section.adminOnly || role === "admin");

export const TABLE_DETAIL_TAB_ALIASES = {
  structure: "files",
  "health-quality": "quality",
  activity: "maintenance",
  lineage: "usage"
} as const;

const TABLE_DETAIL_TABS: Record<string, string> = {
  overview: "overview",
  structure: "files",
  "health-quality": "quality",
  activity: "maintenance",
  maintenance: "maintenance",
  lineage: "usage",
  snapshots: "snapshots",
  cost: "cost"
};

export const translateTableDetailTab = (tab: string | null): string | null => {
  if (tab == null) return null;
  return TABLE_DETAIL_TABS[tab] ?? tab;
};

export const RUNS_NESTED_PATHS = [
  "/runs/jobs/:jobId",
  "/runs/workflows/:presetId",
  "/runs/scanner",
  "/runs/batch",
  "/runs/:runId"
] as const;

export interface AppLocation {
  pathname: string;
  search: string;
  hash: string;
}

const matchPattern = (pattern: string, pathname: string): Record<string, string> | null => {
  const expected = pattern.split("/").filter(Boolean);
  const actual = pathname.split("/").filter(Boolean);
  if (expected.length !== actual.length) return null;
  const params: Record<string, string> = {};
  for (let index = 0; index < expected.length; index += 1) {
    const token = expected[index];
    if (token.startsWith(":")) params[token.slice(1)] = actual[index];
    else if (token !== actual[index]) return null;
  }
  return params;
};

export const firstRunsNestedMatch = (pathname: string): (typeof RUNS_NESTED_PATHS)[number] | null => {
  for (const pattern of RUNS_NESTED_PATHS) {
    if (matchPattern(pattern, pathname)) return pattern;
  }
  return null;
};

const paramsFrom = (search: string): URLSearchParams =>
  new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);

const searchFrom = (params: URLSearchParams): string => {
  const value = params.toString();
  return value ? `?${value}` : "";
};

const paramCount = (params: URLSearchParams): number => Array.from(params.keys()).length;

const searchesEqual = (left: string, right: string): boolean => {
  const a = paramsFrom(left);
  const b = paramsFrom(right);
  if (paramCount(a) !== paramCount(b)) return false;
  for (const [key, value] of a.entries()) {
    if (b.get(key) !== value) return false;
  }
  return true;
};

const finish = (
  pathname: string,
  params: URLSearchParams,
  hash: string,
  input: AppLocation
): AppLocation | null => {
  const next: AppLocation = { pathname, search: searchFrom(params), hash };
  if (next.pathname === input.pathname && next.hash === input.hash && searchesEqual(next.search, input.search)) {
    return null;
  }
  return next;
};

const dropTab = (params: URLSearchParams): void => {
  params.delete("tab");
};

const GOVERNANCE_TABS: Record<string, "triage" | "access" | "signals" | "ownership"> = {
  triage: "triage",
  attention: "triage",
  access: "access",
  policies: "access",
  signals: "signals",
  intelligence: "signals",
  ownership: "ownership",
  inventory: "ownership"
};

const isGovernanceTab = (tab: string | null): tab is string => tab != null && tab in GOVERNANCE_TABS;

const applyGovernance = (
  rawTab: string | null,
  params: URLSearchParams,
  hash: string,
  input: AppLocation
): AppLocation | null => {
  const resolved = rawTab == null ? "triage" : GOVERNANCE_TABS[rawTab];
  if (!resolved) {
    if (rawTab) params.set("legacyTab", rawTab);
    dropTab(params);
    params.set("view", "scan-failed");
    params.set("notice", "governance-unknown");
    return finish("/tables", params, hash, input);
  }
  if (resolved === "signals" && (hash === "#forecast" || params.get("section") === "forecast")) {
    dropTab(params);
    params.delete("section");
    params.set("tab", "storage");
    return finish("/cost", params, hash, input);
  }
  if (resolved === "signals" && (hash === "#lineage" || params.get("section") === "lineage")) {
    dropTab(params);
    params.delete("section");
    params.set("tab", "recommendations");
    params.set("notice", "governance-lineage");
    return finish("/decisions", params, hash, input);
  }
  dropTab(params);
  if (resolved === "access") return finish("/settings/access", params, hash, input);
  if (resolved === "signals") {
    params.set("tab", "recommendations");
    params.set("notice", "governance-signals");
    return finish("/decisions", params, hash, input);
  }
  if (resolved === "ownership") {
    params.set("notice", "governance-ownership");
    return finish("/tables", params, hash, input);
  }
  params.set("view", "scan-failed");
  params.set("notice", "governance-triage");
  return finish("/tables", params, hash, input);
};

const COST_TABS: Record<string, string> = {
  overview: "storage",
  storage: "storage",
  "s3-requests": "requests",
  requests: "requests",
  impact: "storage",
  reads: "reads",
  "aws-integrations": "storage",
  aws: "storage"
};

const normalizeCost = (params: URLSearchParams, hash: string, input: AppLocation): AppLocation | null => {
  const raw = params.get("tab");
  const next = raw == null ? "storage" : (COST_TABS[raw] ?? "storage");
  dropTab(params);
  params.set("tab", next);
  return finish("/cost", params, hash, input);
};

const RUNS_TABS: Record<string, string> = {
  workflows: "workflows",
  runs: "history",
  history: "history",
  actions: "actions",
  holds: "holds"
};

const normalizeRuns = (
  params: URLSearchParams,
  hash: string,
  input: AppLocation,
  missing: "history" | "workflows"
): AppLocation | null => {
  const raw = params.get("tab");
  const next = raw == null ? missing : (RUNS_TABS[raw] ?? missing);
  dropTab(params);
  params.set("tab", next);
  return finish("/runs", params, hash, input);
};

const CONNECTION_TABS: Record<string, string> = {
  sources: "lakehouses",
  lakehouses: "lakehouses",
  destinations: "engines",
  engines: "engines",
  capabilities: "capabilities",
  "ai-providers": "ai-providers",
  slack: "slack"
};

const applyConnections = (
  params: URLSearchParams,
  hash: string,
  input: AppLocation,
  missingBecomesLakehouses: boolean
): AppLocation | null => {
  const raw = params.get("tab");
  if (raw === "ops") {
    dropTab(params);
    return finish("/settings/admin", params, hash, input);
  }
  if (raw == null && !missingBecomesLakehouses) return finish("/settings/connections", params, hash, input);
  const next = raw == null ? "lakehouses" : (CONNECTION_TABS[raw] ?? "lakehouses");
  dropTab(params);
  params.set("tab", next);
  return finish("/settings/connections", params, hash, input);
};

const applySmartMaintenance = (params: URLSearchParams, hash: string, input: AppLocation): AppLocation | null => {
  const raw = params.get("tab");
  dropTab(params);
  params.set("tab", "recommendations");
  return finish("/decisions", params, hash, input);
};

const normalizeDecisions = (params: URLSearchParams, hash: string, input: AppLocation): AppLocation | null => {
  const raw = params.get("tab");
  if (raw == null || raw === "recommendations" || raw === "approvals" || raw === "skipped") {
    return finish("/decisions", params, hash, input);
  }
  return applySmartMaintenance(params, hash, input);
};

const INSIGHTS_TABS: Record<string, string> = {
  duplicates: "duplicates",
  similarity: "duplicates",
  "shared-paths": "shared-paths",
  anomalies: "shared-paths",
  queries: "queries"
};

const applyInsights = (
  params: URLSearchParams,
  hash: string,
  input: AppLocation,
  tab: string
): AppLocation | null => {
  dropTab(params);
  params.set("tab", tab);
  return finish("/insights", params, hash, input);
};

const normalizeInsights = (params: URLSearchParams, hash: string, input: AppLocation): AppLocation | null => {
  const raw = params.get("tab");
  if (raw == null) return null;
  const next = INSIGHTS_TABS[raw] ?? "duplicates";
  return applyInsights(params, hash, input, next);
};

const applyAi = (params: URLSearchParams, hash: string, input: AppLocation): AppLocation | null => {
  const raw = params.get("tab");
  dropTab(params);
  if (raw === "experimental" || raw === "skillsets") params.set("tab", raw);
  params.set("notice", "ai-moved");
  return finish("/settings/ai", params, hash, input);
};

const replacePath = (
  pathname: string,
  params: URLSearchParams,
  hash: string,
  input: AppLocation
): AppLocation | null => finish(pathname, params, hash, input);

export const resolveLegacyRedirect = (location: AppLocation): AppLocation | null => {
  const pathname = location.pathname;
  const hash = location.hash || "";
  const input: AppLocation = { pathname, search: location.search || "", hash };
  const params = paramsFrom(input.search);

  if (matchPattern("/tables/:tableId", pathname)) return null;
  if (matchPattern("/runs/jobs/:jobId", pathname)) return null;
  if (matchPattern("/runs/workflows/:presetId", pathname)) return null;
  if (pathname === "/runs/scanner") return null;
  if (matchPattern("/runs/:runId", pathname)) return null;

  const operationRun = matchPattern("/operations/runs/:runId", pathname);
  if (operationRun) return replacePath(`/runs/${operationRun.runId}`, params, hash, input);
  const preset = matchPattern("/operations/presets/:presetId", pathname);
  if (preset) return replacePath(`/runs/workflows/${preset.presetId}`, params, hash, input);
  const scanRun = matchPattern("/scans/:runId", pathname);
  if (scanRun) return replacePath(`/runs/${scanRun.runId}`, params, hash, input);
  const job = matchPattern("/jobs/:jobId", pathname);
  if (job) return replacePath(`/runs/jobs/${job.jobId}`, params, hash, input);

  if (pathname === "/health") return replacePath("/", params, hash, input);
  if (pathname === "/sla") {
    dropTab(params);
    params.set("view", "freshness");
    return finish("/tables", params, hash, input);
  }
  if (pathname === "/catalogs") return replacePath("/tables", params, hash, input);
  if (pathname === "/search") {
    dropTab(params);
    params.set("notice", "search");
    return finish("/tables", params, hash, input);
  }
  if (pathname === "/dark-tables") {
    dropTab(params);
    params.set("view", "unused");
    return finish("/tables", params, hash, input);
  }
  if (pathname === "/operations/scanner" || pathname === "/scans") {
    return replacePath("/runs/scanner", params, hash, input);
  }
  if (pathname === "/operations") return normalizeRuns(params, hash, input, "workflows");
  if (pathname === "/presets") return normalizeRuns(params, hash, input, "workflows");
  if (pathname === "/control-room") {
    dropTab(params);
    params.set("tab", "holds");
    return finish("/runs", params, hash, input);
  }
  if (pathname === "/smart-maintenance" || pathname === "/optimize") {
    return applySmartMaintenance(params, hash, input);
  }
  if (pathname === "/impact") {
    dropTab(params);
    params.set("tab", "storage");
    return finish("/cost", params, hash, input);
  }
  if (pathname === "/scan-cost") {
    dropTab(params);
    params.set("tab", "reads");
    return finish("/cost", params, hash, input);
  }
  if (pathname === "/cost/aws" || pathname === "/aws") {
    dropTab(params);
    params.set("tab", "lakehouses");
    return finish("/settings/connections", params, hash, input);
  }
  if (pathname === "/similarity") return applyInsights(params, hash, input, "duplicates");
  if (pathname === "/anomalies") return applyInsights(params, hash, input, "shared-paths");
  if (pathname === "/queries") return applyInsights(params, hash, input, "queries");
  if (pathname === "/governance") return applyGovernance(params.get("tab"), params, hash, input);
  if (pathname === "/connections") return applyConnections(params, hash, input, true);
  if (pathname === "/skillsets") {
    dropTab(params);
    params.set("tab", "skillsets");
    return finish("/settings/ai", params, hash, input);
  }
  if (pathname === "/ai") return applyAi(params, hash, input);
  if (pathname === "/settings") return applyConnections(params, hash, input, true);

  if (pathname === "/" || pathname === "/tables") {
    if (pathname === "/tables" && isGovernanceTab(params.get("tab"))) {
      return applyGovernance(params.get("tab"), params, hash, input);
    }
    return null;
  }
  if (pathname === "/runs") return normalizeRuns(params, hash, input, "history");
  if (pathname === "/decisions") return normalizeDecisions(params, hash, input);
  if (pathname === "/cost") return normalizeCost(params, hash, input);
  if (pathname === "/insights") return normalizeInsights(params, hash, input);
  if (pathname === "/settings/connections") return applyConnections(params, hash, input, false);
  if (pathname === "/settings/access" && isGovernanceTab(params.get("tab")) && params.get("tab") !== "access") {
    return applyGovernance(params.get("tab"), params, hash, input);
  }
  if (pathname === "/settings/access" && params.get("tab") === "access") {
    dropTab(params);
    return finish("/settings/access", params, hash, input);
  }
  return null;
};

const PATH_ALIASES: Array<{ prefix: string; href: string }> = [
  { prefix: "/settings", href: "/settings" },
  { prefix: "/tables", href: "/tables" },
  { prefix: "/catalogs", href: "/tables" },
  { prefix: "/search", href: "/tables" },
  { prefix: "/dark-tables", href: "/tables" },
  { prefix: "/sla", href: "/tables" },
  { prefix: "/governance", href: "/tables" },
  { prefix: "/runs", href: "/runs" },
  { prefix: "/operations", href: "/runs" },
  { prefix: "/scans", href: "/runs" },
  { prefix: "/presets", href: "/runs" },
  { prefix: "/jobs", href: "/runs" },
  { prefix: "/control-room", href: "/runs" },
  { prefix: "/decisions", href: "/decisions" },
  { prefix: "/smart-maintenance", href: "/decisions" },
  { prefix: "/optimize", href: "/decisions" },
  { prefix: "/cost", href: "/cost" },
  { prefix: "/impact", href: "/cost" },
  { prefix: "/scan-cost", href: "/cost" },
  { prefix: "/aws", href: "/settings" },
  { prefix: "/insights", href: "/insights" },
  { prefix: "/similarity", href: "/insights" },
  { prefix: "/anomalies", href: "/insights" },
  { prefix: "/queries", href: "/insights" },
  { prefix: "/connections", href: "/settings" },
  { prefix: "/skillsets", href: "/settings" },
  { prefix: "/ai", href: "/settings" }
].sort((left, right) => right.prefix.length - left.prefix.length);

export const destinationHrefForPath = (pathname: string): string => {
  if (pathname === "/" || pathname === "/health") return "/";
  const hit = PATH_ALIASES.find((alias) => pathname === alias.prefix || pathname.startsWith(`${alias.prefix}/`));
  return hit?.href ?? "";
};

export const runsChildTab = (canonicalTab: string | null): string | null => {
  if (canonicalTab === "actions") return "actions";
  if (canonicalTab === "workflows") return null;
  if (canonicalTab === "holds") return null;
  return "runs";
};

export const connectionsChildTab = (canonicalTab: string | null): string => {
  if (canonicalTab === "engines") return "destinations";
  if (canonicalTab === "capabilities" || canonicalTab === "ai-providers" || canonicalTab === "slack") {
    return canonicalTab;
  }
  return "sources";
};

export const searchWithTab = (search: string, tab: string | null, drop: readonly string[] = []): string => {
  const params = paramsFrom(search);
  params.delete("tab");
  for (const key of drop) params.delete(key);
  if (tab) params.set("tab", tab);
  return searchFrom(params);
};
