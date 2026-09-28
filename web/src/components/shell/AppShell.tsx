import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

import { CommandPalette } from "./CommandPalette";
import { DevTokenProdBanner } from "./DevTokenProdBanner";
import { ShellProvider } from "./ShellContext";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import "./shell.css";

export interface AppShellProps {
  children: ReactNode;
}

export const AppShell = ({ children }: AppShellProps) => {
  const { pathname } = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);

  const closeMobileNav = useCallback(() => setMobileOpen(false), []);
  const openCommandPalette = useCallback(() => setPaletteOpen(true), []);

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <ShellProvider value={{ openCommandPalette, closeMobileNav }}>
      <div className="cryo-shell" style={{ background: "var(--color-layout-bg)" }}>
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
      </div>
    </ShellProvider>
  );
};
