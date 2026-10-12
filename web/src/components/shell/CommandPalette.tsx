import { Command } from "cmdk";
import {
  Activity,
  BarChart3,
  Clock,
  Cloud,
  DollarSign,
  EyeOff,
  LayoutDashboard,
  Lightbulb,
  Moon,
  Plug,
  Settings,
  RadioTower,
  Scan,
  ScanSearch,
  Sparkles,
  Sun,
  Zap
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { searchCatalog } from "../../api/client";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { useAuth } from "../../lib/auth";
import { useTheme } from "../../lib/themeContext";
import { Dialog, DialogContent, DialogTitle } from "../ui/dialog";
import { commandPaletteEntries } from "../../lib/routes";
import { useAssistant } from "../assistant/AssistantProvider";
import { useShell } from "./ShellContext";
import { listRecentItems, pushRecentItem } from "./recent";
import { formatSearchSubtitle, getSearchResultHref } from "./search-utils";

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

interface PaletteRoute {
  href: string;
  label: string;
  group: string;
  icon: React.ReactNode;
  adminOnly?: boolean;
  keywords: string;
}

const paletteIcon = (href: string, label: string) => {
  if (label === "Dark tables") return <EyeOff size={14} />;
  if (label === "Control Room") return <RadioTower size={14} />;
  if (label === "Scan cost") return <ScanSearch size={14} />;
  if (label === "SLA") return <Clock size={14} />;
  if (label === "Smart maintenance") return <Cloud size={14} />;
  if (label === "Skillsets") return <Lightbulb size={14} />;
  if (href === "/") return <BarChart3 size={14} />;
  if (href === "/tables") return <LayoutDashboard size={14} />;
  if (href === "/runs") return <Activity size={14} />;
  if (href === "/decisions") return <Cloud size={14} />;
  if (href === "/cost") return <DollarSign size={14} />;
  if (href === "/insights") return <Zap size={14} />;
  if (href.startsWith("/settings")) return <Settings size={14} />;
  return <Activity size={14} />;
};

export const CommandPalette = ({ open, onOpenChange }: CommandPaletteProps) => {
  const navigate = useNavigate();
  const { openAssistant } = useShell();
  const { openBatch } = useAssistant();
  const { api, token, role } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search.trim(), 200);

  useEffect(() => {
    if (!open) setSearch("");
  }, [open]);

  const routes = useMemo<PaletteRoute[]>(
    () =>
      commandPaletteEntries(role).map((entry) => ({
        href: entry.href,
        label: entry.label,
        group: entry.group,
        icon: paletteIcon(entry.href, entry.label),
        adminOnly: entry.adminOnly,
        keywords: entry.keywords
      })),
    [role]
  );

  const tableSearch = useMemo(() => {
    if (debouncedSearch.length < 2) return null;
    return searchCatalog(api, { q: debouncedSearch, limit: 8 });
  }, [api, debouncedSearch]);

  const [tableHits, setTableHits] = useState<Awaited<ReturnType<typeof searchCatalog>> | null>(null);
  const [searchFailed, setSearchFailed] = useState(false);

  useEffect(() => {
    if (!tableSearch) {
      setTableHits(null);
      setSearchFailed(false);
      return;
    }
    let cancelled = false;
    tableSearch
      .then((data) => {
        if (!cancelled) {
          setTableHits(data);
          setSearchFailed(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setTableHits(null);
          setSearchFailed(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [tableSearch]);

  const recent = search.trim()
    ? []
    : listRecentItems()
        .filter((item) => !["/runs", "/tables", "/cost", "/insights", "/decisions", "/settings", "/"].includes(item.href))
        .slice(0, 6);

  const go = (href: string, label: string, type: "route" | "table" | "run" | "preset" = "route") => {
    pushRecentItem({ type, label, href });
    onOpenChange(false);
    navigate(href);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="cryo-cmdk" aria-describedby={undefined}>
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <Command label="Command palette" shouldFilter>
          <Command.Input
            placeholder="Search actions, routes, tables…"
            value={search}
            onValueChange={setSearch}
          />
          <Command.List>
            <Command.Empty>No results found.</Command.Empty>

            {recent.length > 0 ? (
              <Command.Group heading="Recent">
                {recent.map((item) => (
                  <Command.Item
                    key={item.href}
                    value={`recent ${item.label} ${item.href}`}
                    onSelect={() => go(item.href, item.label, item.type)}
                  >
                    <Clock size={14} aria-hidden />
                    {item.label}
                  </Command.Item>
                ))}
              </Command.Group>
            ) : null}

            <Command.Group heading="Actions">
              {search.trim() ? (
                <Command.Item
                  value={`ask cryo ${search}`}
                  onSelect={() => {
                    const question = search.trim();
                    onOpenChange(false);
                    openAssistant(question);
                  }}
                >
                  <Sparkles size={14} aria-hidden />
                  Ask Cryo: {search.trim()}
                </Command.Item>
              ) : null}
              <Command.Item
                value="review batch maintenance"
                onSelect={() => {
                  onOpenChange(false);
                  openBatch();
                }}
              >
                <Sparkles size={14} aria-hidden />
                Review batch
              </Command.Item>
              <Command.Item
                value="run scan trigger scanner"
                onSelect={() => go("/runs/scanner", "Scanner", "route")}
              >
                <Scan size={14} aria-hidden />
                Run scan
              </Command.Item>

              <Command.Item
                value="toggle theme dark light mode"
                onSelect={() => {
                  toggleTheme();
                  onOpenChange(false);
                }}
              >
                {theme === "light" ? <Moon size={14} /> : <Sun size={14} />}
                Toggle theme
              </Command.Item>
              <Command.Item
                value="open connections settings"
                onSelect={() => go("/settings/connections", "Connections")}
              >
                <Plug size={14} aria-hidden />
                Open connections
              </Command.Item>
            </Command.Group>

            <Command.Group heading="Navigate">
              {routes.map((route) => (
                <Command.Item
                  key={route.href}
                  value={`${route.label} ${route.keywords} ${route.href}`}
                  onSelect={() => go(route.href, route.label)}
                >
                  {route.icon}
                  {route.label}
                  <span className="cryo-shell__search-item-meta">{route.group}</span>
                </Command.Item>
              ))}
              <Command.Item
                value="operations scanner workflow runs"
                onSelect={() => go("/runs/scanner", "Scanner workflow")}
              >
                <Scan size={14} aria-hidden />
                Operations › Scanner
              </Command.Item>
            </Command.Group>

            {searchFailed ? (
              <Command.Group heading="Tables">
                <Command.Item value={`${search} search unavailable`} disabled>
                  Search unavailable
                </Command.Item>
              </Command.Group>
            ) : null}
            {tableHits && tableHits.groups.some((g) => g.items.length > 0) ? (
              <Command.Group heading="Tables">
                {tableHits.groups.flatMap((group) =>
                  group.items.map((hit) => {
                    const href = getSearchResultHref(hit);
                    const subtitle = formatSearchSubtitle(hit);
                    return (
                      <Command.Item
                        key={`${group.type}-${hit.id}`}
                        value={`${search} ${hit.name} ${hit.qualified_name ?? ""} ${subtitle ?? ""} table`}
                        onSelect={() => go(href, hit.qualified_name ?? hit.name, "table")}
                      >
                        <LayoutDashboard size={14} aria-hidden />
                        {hit.qualified_name ?? hit.name}
                        {subtitle ? (
                          <span className="cryo-shell__search-item-meta">{subtitle}</span>
                        ) : null}
                      </Command.Item>
                    );
                  })
                )}
              </Command.Group>
            ) : null}

          </Command.List>

          <div className="cryo-cmdk__hint">
            <span>↑↓ navigate · ↵ select · esc close</span>
            <span>{token ? "Fleet search shares catalog index" : ""}</span>
          </div>
        </Command>
      </DialogContent>
    </Dialog>
  );
};
