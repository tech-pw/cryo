import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

import { cn } from "../../lib/cn";
import { AskCryoDrawer } from "../assistant/AskCryoDrawer";
import { AssistantProvider, useAssistant } from "../assistant/AssistantProvider";
import { CommandPalette } from "./CommandPalette";
import { DevTokenProdBanner } from "./DevTokenProdBanner";
import { ShellProvider } from "./ShellContext";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import "./shell.css";

const SIDEBAR_COLLAPSED_KEY = "cryo.sidebar.collapsed";

const readSidebarCollapsed = (): boolean => {
  try {
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
};

const writeSidebarCollapsed = (collapsed: boolean): void => {
  try {
    window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch {
    return;
  }
};

const SIDEBAR_COLLAPSED_CSS = `
.cryo-shell--sidebar-collapsed .cryo-shell__sidebar {
  flex-basis: 56px;
  width: 56px;
}
.cryo-shell--sidebar-collapsed .cryo-shell__brand {
  justify-content: center;
  padding-inline: 0;
}
.cryo-shell--sidebar-collapsed .cryo-shell__brand-copy,
.cryo-shell--sidebar-collapsed .cryo-shell__nav-group-label,
.cryo-shell--sidebar-collapsed .cryo-shell__nav-link > span {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
.cryo-shell--sidebar-collapsed .cryo-shell__nav-link {
  justify-content: center;
  gap: 0;
  margin-inline: 6px;
  padding-inline: 0;
}
@media (max-width: 768px) {
  .cryo-shell--sidebar-collapsed .cryo-shell__sidebar {
    flex-basis: 224px;
    width: 224px;
  }
  .cryo-shell--sidebar-collapsed .cryo-shell__brand {
    justify-content: flex-start;
    padding-inline: 16px;
  }
  .cryo-shell--sidebar-collapsed .cryo-shell__brand-copy,
  .cryo-shell--sidebar-collapsed .cryo-shell__nav-group-label,
  .cryo-shell--sidebar-collapsed .cryo-shell__nav-link > span {
    position: static;
    width: auto;
    height: auto;
    margin: 0;
    overflow: visible;
    clip: auto;
    white-space: normal;
  }
  .cryo-shell--sidebar-collapsed .cryo-shell__nav-link {
    justify-content: flex-start;
    gap: 10px;
    margin: 2px 8px;
    padding: 8px 10px;
  }
}
`;

export interface AppShellProps {
  children: ReactNode;
}

export const AppShell = ({ children }: AppShellProps) => (
  <AssistantProvider>
    <AppShellFrame>{children}</AppShellFrame>
  </AssistantProvider>
);

const AppShellFrame = ({ children }: AppShellProps) => {
  const { pathname } = useLocation();
  const { openAssistant, toggle } = useAssistant();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(readSidebarCollapsed);

  const closeMobileNav = useCallback(() => setMobileOpen(false), []);
  const openCommandPalette = useCallback(() => setPaletteOpen(true), []);
  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((current) => {
      const next = !current;
      writeSidebarCollapsed(next);
      return next;
    });
  }, []);

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen(true);
      }
      if (event.repeat) return;
      if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === "i") {
        event.preventDefault();
        toggle();
      }
      if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === "b") {
        event.preventDefault();
        toggleSidebar();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [toggle, toggleSidebar]);

  return (
    <ShellProvider value={{ openCommandPalette, closeMobileNav, openAssistant }}>
      <div
        className={cn("cryo-shell", sidebarCollapsed && "cryo-shell--sidebar-collapsed")}
        data-sidebar-collapsed={sidebarCollapsed ? "true" : "false"}
        style={{ background: "var(--color-layout-bg)" }}
      >
        <style>{SIDEBAR_COLLAPSED_CSS}</style>
        {mobileOpen ? (
          <button
            type="button"
            className="cryo-shell__backdrop"
            aria-label="Close navigation menu"
            onClick={closeMobileNav}
          />
        ) : null}

        <Sidebar pathname={pathname} mobileOpen={mobileOpen} onNavigate={closeMobileNav} />

        <div className="cryo-shell__main">
          <Topbar onMenuClick={() => setMobileOpen(true)} />
          <main
            className="cryo-shell__content"
            id="main-content"
            tabIndex={-1}
            style={{ background: "var(--color-layout-bg)" }}
          >
            <DevTokenProdBanner />
            {children}
          </main>
        </div>

        <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
        <AskCryoDrawer />
      </div>
    </ShellProvider>
  );
};
