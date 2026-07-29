import { type ReactNode } from "react";
import { PageBreadcrumbs, type Crumb } from "../PageBreadcrumbs";
import { PageTitle } from "../PageTitle";
import { cn } from "../../lib/cn";

export interface PageHeaderProps {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  breadcrumbs?: ReactNode;
  rootCrumb?: Crumb;
  className?: string;
}

/** Page chrome with a single visible h1 — replaces the duplicated antd PageHeader gradually. */
export function PageHeader({ title, subtitle, actions, breadcrumbs, rootCrumb, className }: PageHeaderProps) {
  const crumbs = breadcrumbs ?? <PageBreadcrumbs root={rootCrumb} />;

  return (
    <header className={cn("mb-4", className)}>
      <PageTitle title={title} />
      {crumbs ? <div className="mb-3 text-xs text-muted-foreground [&_.ant-breadcrumb]:mb-0">{crumbs}</div> : null}
      <div className="flex min-h-12 flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 max-w-[68ch]">
          <h1 className="m-0 text-2xl font-semibold leading-tight tracking-[-0.02em] text-foreground">{title}</h1>
          {subtitle ? <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </header>
  );
}
