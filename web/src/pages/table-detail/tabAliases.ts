/** Old table-detail tab and hash values. Tab translation matches runbook §4.2. */

export const TABLE_DETAIL_TABS = [
  "overview",
  "files",
  "snapshots",
  "maintenance",
  "usage",
  "quality",
  "cost"
] as const;

export type TableDetailTab = (typeof TABLE_DETAIL_TABS)[number];

export const TABLE_TAB_ALIASES: Record<string, TableDetailTab> = {
  overview: "overview",
  structure: "files",
  files: "files",
  "health-quality": "quality",
  quality: "quality",
  activity: "maintenance",
  maintenance: "maintenance",
  lineage: "usage",
  usage: "usage",
  snapshots: "snapshots",
  cost: "cost"
};

/** `#hq-*` anchors from the old Health & Quality tab. */
export const TABLE_HASH_ALIASES: Record<string, { tab: TableDetailTab; hash: string }> = {
  "hq-properties": { tab: "files", hash: "files-properties" },
  "hq-checklist": { tab: "quality", hash: "quality-checklist" },
  "hq-quality": { tab: "quality", hash: "quality-checks" },
  "hq-compaction": { tab: "maintenance", hash: "maintenance-compaction" },
  "hq-ai-signals": { tab: "quality", hash: "quality-experimental" }
};

export const canonicalTableTab = (value: string | null): TableDetailTab => {
  if (!value) return "overview";
  return TABLE_TAB_ALIASES[value] ?? (TABLE_DETAIL_TABS.includes(value as TableDetailTab) ? (value as TableDetailTab) : "overview");
};

export const translateTableDetailLocation = (
  search: URLSearchParams,
  hash: string
): { search: URLSearchParams; hash: string } => {
  const next = new URLSearchParams(search);
  const requested = next.get("tab");
  const canonical = canonicalTableTab(requested);
  const bare = hash.replace(/^#/, "");
  const mapped = bare.startsWith("hq-") ? TABLE_HASH_ALIASES[bare] : undefined;
  const tab = mapped?.tab ?? canonical;
  if (tab === "overview") next.delete("tab");
  else next.set("tab", tab);
  if (requested && !TABLE_TAB_ALIASES[requested] && !mapped) {
    next.delete("tab");
  }
  const nextHash = mapped ? (mapped.hash ? `#${mapped.hash}` : "") : bare.startsWith("hq-") ? "" : hash;
  return { search: next, hash: nextHash };
};
