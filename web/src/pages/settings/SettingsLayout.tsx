import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { PageHeader } from "../../components/ui/page-header";
import { cn } from "../../lib/cn";
import { useAuth } from "../../lib/auth";
import { visibleSettingsSections } from "../../lib/routes";

export function SettingsLayout({
  children,
  title = "Settings",
  subtitle = "What is connected, and who may call the model?"
}: {
  children?: ReactNode;
  title?: string;
  subtitle?: string;
}) {
  const { role } = useAuth();
  const { pathname } = useLocation();
  const sections = visibleSettingsSections(role);
  const current = sections.find((section) => pathname.startsWith(section.path))?.id ?? "connections";

  return (
    <div>
      <PageHeader
        className="settings-page-header"
        title={title}
        subtitle={subtitle}
      />
      <div className="mt-5 flex flex-col gap-6 md:flex-row md:items-start">
        <nav
          aria-label="Settings"
          className={cn(
            "settings-nav flex w-full shrink-0 flex-row gap-1 overflow-x-auto p-1.5 md:w-56 md:flex-col md:overflow-visible",
            "rounded-[var(--radius-lg)] border border-border",
            "bg-[var(--color-surface-raised)] shadow-[var(--shadow-card)]"
          )}
        >
          {sections.map((section) => {
            const selected = current === section.id;
            return (
              <Link
                key={section.id}
                to={section.path}
                aria-current={selected ? "page" : undefined}
                className={cn(
                  "relative shrink-0 rounded-[var(--radius-default)] px-3 py-2 text-left text-sm text-foreground",
                  "transition-[background-color,color,transform,box-shadow] duration-[var(--motion-fast)] ease-[var(--ease-out)]",
                  "active:scale-[0.97] motion-reduce:transition-none motion-reduce:active:scale-100",
                  "focus-visible:outline-none focus-visible:ring-[var(--focus-ring-width)] focus-visible:ring-[var(--focus-ring-color)]",
                  selected
                    ? "bg-[var(--color-surface-raised)] font-medium text-foreground shadow-[inset_2px_0_0_var(--color-primary),0_1px_1px_rgb(0_0_0/0.04)]"
                    : "text-muted-foreground hover:bg-[var(--color-surface-sunken)] hover:text-foreground"
                )}
              >
                {section.label}
              </Link>
            );
          })}
        </nav>
        <div className="min-w-0 flex-1 text-sm text-foreground">{children}</div>
      </div>
    </div>
  );
}
