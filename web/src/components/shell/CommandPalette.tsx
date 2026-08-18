import { Command } from "cmdk";
import {
  Activity,
  BarChart3,
  Bot,
  Clock,
  Cloud,
  DollarSign,
  LayoutDashboard,
  Lightbulb,
  Moon,
  Plug,
  Plus,
  Scan,
  Shield,
  Sun,
  TriangleAlert,
  Zap
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { searchCatalog } from "../../api/client";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { useAuth } from "../../lib/auth";
import { useTheme } from "../../lib/themeContext";
import { Dialog, DialogContent, DialogTitle } from "../ui/dialog";
import { NAV_GROUPS } from "./nav-config";
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
}

const ROUTE_ICONS: Record<string, React.ReactNode> = {
  "/health": <BarChart3 size={14} />,
  "/sla": <Clock size={14} />,
  "/catalogs": <LayoutDashboard size={14} />,
  "/operations": <Activity size={14} />,
  "/smart-maintenance": <Cloud size={14} />,
  "/ai": <Bot size={14} />,
  "/governance": <Shield size={14} />,
  "/anomalies": <TriangleAlert size={14} />,
  "/queries": <Zap size={14} />,
  "/cost": <DollarSign size={14} />,
  "/skillsets": <Lightbulb size={14} />,
  "/connections": <Plug size={14} />
};

export const CommandPalette = ({ open, onOpenChange }: CommandPaletteProps) => {
  const navigate = useNavigate();
  const { api, token, role } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search.trim(), 200);

  useEffect(() => {
    if (!open) setSearch("");
  }, [open]);

  const routes = useMemo<PaletteRoute[]>(
    () =>
      NAV_GROUPS.flatMap((group) =>
        group.items
          .filter((item) => !item.adminOnly || role === "admin")
          .map((item) => ({
            href: item.href,
            label: item.label,
            group: group.label,
            icon: ROUTE_ICONS[item.href] ?? <Activity size={14} />,
            adminOnly: item.adminOnly
          }))
      ),
    [role]
  );

  const tableSearch = useMemo(() => {
    if (debouncedSearch.length < 2) return null;
    return searchCatalog(api, { q: debouncedSearch, limit: 8 });
  }, [api, debouncedSearch]);

  const [tableHits, setTableHits] = useState<Awaited<ReturnType<typeof searchCatalog>> | null>(null);

  useEffect(() => {
    if (!tableSearch) {
      setTableHits(null);
      return;
    }
    let cancelled = false;
    tableSearch
      .then((data) => {
        if (!cancelled) setTableHits(data);
      })
      .catch(() => {
        if (!cancelled) setTableHits(null);
      });
    return () => {
      cancelled = true;
    };
  }, [tableSearch]);

  const recent = listRecentItems().slice(0, 6);

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

            <Command.Group heading="Actions">
              <Command.Item
                value="run scan trigger scanner"
                onSelect={() => go("/scans", "Scanner", "route")}
              >
                <Scan size={14} aria-hidden />
                Run scan
              </Command.Item>
              <Command.Item
                value="create preset new maintenance"
                onSelect={() => go("/presets", "Presets", "preset")}
              >
                <Plus size={14} aria-hidden />
                Create preset
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
                onSelect={() => go("/connections", "Connections")}
              >
                <Plug size={14} aria-hidden />
                Open connections
              </Command.Item>
            </Command.Group>

            <Command.Group heading="Navigate">
              {routes.map((route) => (
                <Command.Item
                  key={route.href}
                  value={`${route.label} ${route.group} ${route.href}`}
                  onSelect={() => go(route.href, route.label)}
                >
                  {route.icon}
                  {route.label}
                  <span className="cryo-shell__search-item-meta">{route.group}</span>
                </Command.Item>
              ))}
              <Command.Item
                value="operations scanner workflow"
                onSelect={() => go("/operations/scanner", "Scanner workflow")}
              >
                <Scan size={14} aria-hidden />
                Operations › Scanner
              </Command.Item>
            </Command.Group>

            {tableHits && tableHits.groups.some((g) => g.items.length > 0) ? (
              <Command.Group heading="Tables">
                {tableHits.groups.flatMap((group) =>
                  group.items.map((hit) => {
                    const href = getSearchResultHref(hit);
                    const subtitle = formatSearchSubtitle(hit);
                    return (
                      <Command.Item
                        key={`${group.type}-${hit.id}`}
                        value={`${hit.qualified_name ?? hit.name} ${subtitle ?? ""} table search`}
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
