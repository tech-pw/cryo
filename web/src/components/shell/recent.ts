export type RecentItemType = "table" | "run" | "preset" | "route";

export interface RecentItem {
  type: RecentItemType;
  label: string;
  href: string;
  visitedAt: number;
}

const STORAGE_KEY = "cryo.shell.recent";
const MAX_RECENT = 12;

const readRecent = (): RecentItem[] => {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as RecentItem[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

const writeRecent = (items: RecentItem[]) => {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(items.slice(0, MAX_RECENT)));
  } catch {
    // sessionStorage unavailable
  }
};

export const listRecentItems = (): RecentItem[] =>
  readRecent().sort((a, b) => b.visitedAt - a.visitedAt);

export const pushRecentItem = (item: Omit<RecentItem, "visitedAt">) => {
  const next: RecentItem = { ...item, visitedAt: Date.now() };
  const deduped = readRecent().filter((existing) => existing.href !== next.href);
  writeRecent([next, ...deduped]);
};

export const clearRecentItems = () => {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
};
