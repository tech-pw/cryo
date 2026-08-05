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
    <header className={cn("cryo-page-header", className)}>
      <PageTitle title={title} />
      {crumbs ? <div className="cryo-page-header__breadcrumbs">{crumbs}</div> : null}
      <div className="cryo-page-header__body">
        <div className="cryo-page-header__copy">
          <h1 className="cryo-page-header__title">{title}</h1>
          {subtitle ? <p className="cryo-page-header__subtitle">{subtitle}</p> : null}
        </div>
        {actions ? <div className="cryo-page-header__actions">{actions}</div> : null}
      </div>
    </header>
  );
}
