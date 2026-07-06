import { Card } from "antd";
import type { ReactNode } from "react";

interface PanelCardProps {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  quiet?: boolean;
  children: ReactNode;
}

export const PanelCard = ({ title, subtitle, actions, quiet = false, children }: PanelCardProps) => (
  <Card className={`td-chart-card${quiet ? " td-chart-card-quiet" : ""}`} classNames={{ body: "td-chart-card-body" }}>
    <div className="td-chart-card-header">
      <span className="td-chart-card-title">{title}</span>
      {subtitle ? <span className="td-chart-card-subtitle">{subtitle}</span> : null}
      {actions ? <div className="td-chart-card-actions">{actions}</div> : null}
    </div>
    <div className="td-chart-card-content">{children}</div>
  </Card>
);
