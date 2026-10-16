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
      <div className="mt-5 flex flex-col gap-5 xl:flex-row xl:items-start xl:gap-8">
        <nav
          aria-label="Settings"
          className={cn(
            "settings-nav flex w-full shrink-0 flex-row gap-1 overflow-x-auto border-b border-border",
            "xl:sticky xl:top-0 xl:w-48 xl:flex-col xl:overflow-visible xl:border-b-0"
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
                  "relative -mb-px shrink-0 whitespace-nowrap border-b-2 px-3 py-2 text-left text-sm",
                  "xl:mb-0 xl:rounded-[var(--radius-default)] xl:border-b-0",
                  "transition-[background-color,color,border-color] duration-[var(--motion-fast)] ease-[var(--ease-out)] motion-reduce:transition-none",
                  "focus-visible:outline-none focus-visible:ring-[var(--focus-ring-width)] focus-visible:ring-[var(--focus-ring-color)]",
                  selected
                    ? "border-[var(--color-primary)] font-medium text-[var(--color-on-tint)] xl:bg-[var(--color-primary-soft)] xl:shadow-[inset_2px_0_0_var(--color-primary)]"
                    : "border-transparent text-muted-foreground hover:text-foreground xl:hover:bg-[var(--color-surface-sunken)]"
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
