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
      aria-label="Primary navigation"
    >
      <div className="cryo-shell__brand">
        <BrandIcon size={18} aria-hidden />
        <span>Cryo</span>
      </div>
      <nav className="cryo-shell__nav">
        {NAV_GROUPS.map((group) => {
          const items = group.items.filter((item) => !item.adminOnly || role === "admin");
          if (items.length === 0) return null;

          return (
            <div key={group.label} className="cryo-shell__nav-group">
              <div className="cryo-shell__nav-group-label">{group.label}</div>
              {items.map((item) => {
                const Icon = item.icon;
                const isActive = activeHref === item.href;
                return (
                  <NavLink
                    key={item.href}
                    to={item.href}
                    className={cn("cryo-shell__nav-link", isActive && "cryo-shell__nav-link--active")}
                    onClick={onNavigate}
                    end={item.href === "/operations"}
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
    </aside>
  );
};
