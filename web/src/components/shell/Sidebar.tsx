import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink } from "react-router-dom";

import { useAuth } from "../../lib/auth";
import { CryoMark } from "../brand/CryoMark";
import { cn } from "../../lib/cn";
import { NAV_GROUPS, resolveActiveNavHref } from "./nav-config";
import { VersionFooter } from "./VersionFooter";

export interface SidebarProps {
  pathname: string;
  mobileOpen: boolean;
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
  onNavigate?: () => void;
}

export const Sidebar = ({ pathname, mobileOpen, collapsed = false, onToggleCollapsed, onNavigate }: SidebarProps) => {
  const { role } = useAuth();
  const activeHref = resolveActiveNavHref(pathname);
  const [navScrolled, setNavScrolled] = useState(false);

  useEffect(() => {
    const nav = document.querySelector<HTMLElement>("#primary-navigation .cryo-shell__nav");
    if (!nav) return;
    const onScroll = () => setNavScrolled(nav.scrollTop > 0);
    onScroll();
    nav.addEventListener("scroll", onScroll, { passive: true });
    return () => nav.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <aside
      className={cn("cryo-shell__sidebar", mobileOpen && "cryo-shell__sidebar--open", navScrolled && "cryo-shell__sidebar--scrolled")}
      id="primary-navigation"
      aria-label="Primary navigation"
    >
      <Link to="/" className="cryo-shell__brand" aria-label="Cryo, go to home" onClick={onNavigate}>
        <CryoMark className="cryo-shell__brand-mark" aria-hidden />
        <span className="cryo-shell__brand-copy">
          <span className="cryo-shell__brand-cr">Cr</span>
          <span className="cryo-shell__brand-yo">yo</span>
        </span>
      </Link>
      <nav className="cryo-shell__nav">
        {NAV_GROUPS.map((group, index) => {
          const items = group.items.filter((item) => !item.adminOnly || role === "admin");
          if (items.length === 0) return null;

          return (
            <div key={group.label || `pinned-${index}`} className="cryo-shell__nav-group">
              {group.label ? <div className="cryo-shell__nav-group-label">{group.label}</div> : null}
              {items.map((item) => {
                const Icon = item.icon;
                const isActive = activeHref === item.href;
                return (
                  <NavLink
                    key={item.href}
                    to={item.href}
                    className={cn("cryo-shell__nav-link", isActive && "cryo-shell__nav-link--active")}
                    onClick={onNavigate}
                    end={item.href === "/"}
                    title={item.label}
                  >
                    <Icon size={16} className="cryo-shell__nav-icon" aria-hidden />
                    <span>{item.label}</span>
                  </NavLink>
                );
              })}
            </div>
          );
        })}
      </nav>
      <VersionFooter onNavigate={onNavigate} />
      {onToggleCollapsed ? (
        <button
          type="button"
          className="cryo-shell__collapse"
          aria-expanded={!collapsed}
          aria-controls="primary-navigation"
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          onClick={onToggleCollapsed}
        >
          {collapsed ? <PanelLeftOpen size={16} aria-hidden /> : <PanelLeftClose size={16} aria-hidden />}
          <span>{collapsed ? "Expand sidebar" : "Collapse sidebar"}</span>
        </button>
      ) : null}
    </aside>
  );
};
