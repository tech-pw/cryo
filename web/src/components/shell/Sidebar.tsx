import { NavLink } from "react-router-dom";

import { useAuth } from "../../lib/auth";
import { cn } from "../../lib/cn";
import { BRAND_ICON, NAV_GROUPS, resolveActiveNavHref } from "./nav-config";

export interface SidebarProps {
  pathname: string;
  mobileOpen: boolean;
  onNavigate?: () => void;
}

export const Sidebar = ({ pathname, mobileOpen, onNavigate }: SidebarProps) => {
  const { role } = useAuth();
  const activeHref = resolveActiveNavHref(pathname);
  const BrandIcon = BRAND_ICON;

  return (
    <aside
      className={cn("cryo-shell__sidebar", mobileOpen && "cryo-shell__sidebar--open")}
      style={{
        background: "var(--sidebar-bg)",
        borderRight: "1px solid var(--color-border-default)"
      }}
      aria-label="Primary navigation"
    >
      <div className="cryo-shell__brand">
        <span
          className="cryo-shell__brand-mark"
          style={{
            background: "var(--color-primary)",
            color: "var(--color-primary-foreground)",
            borderColor: "transparent",
            boxShadow: "none"
          }}
        >
          <BrandIcon size={18} aria-hidden />
        </span>
        <span className="cryo-shell__brand-copy">
          <strong>Cryo</strong>
          <small style={{ color: "var(--sidebar-text)", letterSpacing: 0 }}>Iceberg control plane</small>
        </span>
      </div>
      <nav className="cryo-shell__nav">
        {NAV_GROUPS.map((group) => {
          const items = group.items.filter((item) => !item.adminOnly || role === "admin");
          if (items.length === 0) return null;

          return (
            <div key={group.label} className="cryo-shell__nav-group">
              <div
                className="cryo-shell__nav-group-label"
                style={{
                  color: "var(--sidebar-text)",
                  fontWeight: 500,
                  letterSpacing: 0,
                  textTransform: "none"
                }}
              >
                {group.label}
              </div>
              {items.map((item) => {
                const Icon = item.icon;
                const isActive = activeHref === item.href;
                return (
                  <NavLink
                    key={item.href}
                    to={item.href}
                    className={cn(
                      "cryo-shell__nav-link focus-visible:!outline focus-visible:!outline-2 focus-visible:!outline-offset-2 focus-visible:!outline-[var(--focus-ring-color)]",
                      isActive && "cryo-shell__nav-link--active"
                    )}
                    style={{
                      overflow: "hidden",
                      borderRadius: "var(--radius-default)",
                      ...(isActive
                        ? {
                            background: "var(--sidebar-active-bg)",
                            borderColor: "transparent",
                            color: "var(--sidebar-text-active)"
                          }
                        : null)
                    }}
                    onClick={onNavigate}
                    end={item.href === "/operations"}
                  >
                    <Icon
                      size={16}
                      className="cryo-shell__nav-icon"
                      style={isActive ? { color: "var(--sidebar-text-active)", opacity: 1 } : undefined}
                      aria-hidden
                    />
                    <span>{item.label}</span>
                  </NavLink>
                );
              })}
            </div>
          );
        })}
      </nav>
    </aside>
  );
};
