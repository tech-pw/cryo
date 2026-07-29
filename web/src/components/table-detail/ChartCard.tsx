import { type ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface ChartCardProps {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  quiet?: boolean;
  contentClassName?: string;
}

/** Table-detail chart shell — replaces antd Card + `td-chart-card` classNames. */
export const ChartCard = ({
  title,
  subtitle,
  actions,
  children,
  className,
  quiet,
  contentClassName
}: ChartCardProps) => (
  <div className={cn("td-chart-card", quiet && "td-chart-card-quiet", className)}>
    <div className="td-chart-card-body">
      {title || subtitle || actions ? (
        <div className="td-chart-card-header">
          {title ? <span className="td-chart-card-title">{title}</span> : null}
          {subtitle ? <span className="td-chart-card-subtitle">{subtitle}</span> : null}
          {actions ? <div className="td-chart-card-actions">{actions}</div> : null}
        </div>
      ) : null}
      <div className={cn("td-chart-card-content", contentClassName)}>{children}</div>
    </div>
  </div>
);
