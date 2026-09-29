import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { PageHeader } from "../../components/ui/page-header";
import { cn } from "../../lib/cn";
import { useAuth } from "../../lib/auth";
import { visibleSettingsSections } from "../../lib/routes";

export function SettingsLayout({ children }: { children?: ReactNode }) {
  const { role } = useAuth();
  const { pathname } = useLocation();
  const sections = visibleSettingsSections(role);
  const current = sections.find((section) => pathname.startsWith(section.path))?.id ?? "connections";

  return (
    <div>
      <PageHeader title="Settings" subtitle="What is connected, and who may call the model?" />
      <div className="mt-6 flex flex-col gap-6 md:flex-row md:items-start">
        <nav aria-label="Settings" className="flex w-56 shrink-0 flex-col gap-1">
          {sections.map((section) => {
            const selected = current === section.id;
            return (
              <Link
                key={section.id}
                to={section.path}
                aria-current={selected ? "page" : undefined}
                className={cn(
                  "rounded-[var(--radius-default)] px-3 py-2 text-left text-sm text-foreground",
                  "focus-visible:outline-none focus-visible:ring-[var(--focus-ring-width)] focus-visible:ring-[var(--focus-ring-color)]",
                  selected
                    ? "bg-[var(--color-primary-soft)] font-medium text-[var(--color-on-tint)]"
                    : "hover:bg-[var(--color-surface-sunken)]"
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
