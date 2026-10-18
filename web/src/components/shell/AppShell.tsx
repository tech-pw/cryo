import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
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
  height: 56px;
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
.cryo-shell--sidebar-collapsed .cryo-shell__nav-link,
.cryo-shell--sidebar-collapsed .cryo-shell__collapse {
  justify-content: center;
  gap: 0;
  margin-inline: 6px;
  /* 2px keeps the active inset rail inside the pill; 0 clips it. */
  padding-inline: 2px 0;
}
.cryo-shell--sidebar-collapsed .cryo-shell__collapse > span {
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
  .cryo-shell--sidebar-collapsed .cryo-shell__nav-link,
  .cryo-shell--sidebar-collapsed .cryo-shell__collapse {
    justify-content: flex-start;
    gap: 10px;
    margin: 2px 8px;
    padding: 8px 10px;
  }
  .cryo-shell--sidebar-collapsed .cryo-shell__collapse > span {
    position: static;
    width: auto;
    height: auto;
    margin: 0;
    overflow: visible;
    clip: auto;
    white-space: normal;
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
  const { open, panel, openAssistant, setOpen } = useAssistant();
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

  const mobileWasOpen = useRef(false);

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (mobileOpen) {
      mobileWasOpen.current = true;
      document.getElementById("primary-navigation")?.querySelector<HTMLElement>("a")?.focus();
      const onTab = (event: KeyboardEvent) => {
        if (event.key !== "Tab") return;
        const nav = document.getElementById("primary-navigation");
        const links = nav ? [...nav.querySelectorAll<HTMLElement>("a")] : [];
        if (links.length === 0) return;
        const first = links[0];
        const last = links[links.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      };
      document.addEventListener("keydown", onTab);
      return () => document.removeEventListener("keydown", onTab);
    }
    if (mobileWasOpen.current) {
      mobileWasOpen.current = false;
      document.querySelector<HTMLElement>(".cryo-shell__menu-btn")?.focus();
    }
  }, [mobileOpen]);

  useEffect(() => {
    const query = window.matchMedia("(max-width: 768px)");
    const onChange = () => {
      if (!query.matches) setMobileOpen(false);
    };
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && mobileOpen) {
        event.preventDefault();
        setMobileOpen(false);
        return;
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen(true);
      }
      if (event.repeat) return;
      if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === "i") {
        event.preventDefault();
        if (open && panel === "ask") setOpen(false);
        else openAssistant();
      }
      if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === "b") {
        event.preventDefault();
        toggleSidebar();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mobileOpen, open, panel, openAssistant, setOpen, toggleSidebar]);

  return (
    <ShellProvider value={{ openCommandPalette, closeMobileNav, openAssistant }}>
      <div
        className={cn("cryo-shell", sidebarCollapsed && "cryo-shell--sidebar-collapsed")}
        data-sidebar-collapsed={sidebarCollapsed ? "true" : "false"}
        style={{ background: "var(--color-layout-bg)" }}
      >
        <style>{SIDEBAR_COLLAPSED_CSS}</style>
        <a href="#main-content" className="cryo-shell__skip">
          Skip to main content
        </a>
        {mobileOpen ? (
          <button
            type="button"
            className="cryo-shell__backdrop"
            aria-label="Close navigation menu"
            tabIndex={-1}
            onClick={closeMobileNav}
          />
        ) : null}

        <Sidebar
          pathname={pathname}
          mobileOpen={mobileOpen}
          collapsed={sidebarCollapsed}
          onToggleCollapsed={toggleSidebar}
          onNavigate={closeMobileNav}
        />

        <div className="cryo-shell__main" inert={mobileOpen ? true : undefined}>
          <Topbar menuOpen={mobileOpen} onMenuClick={() => setMobileOpen((open) => !open)} />
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
