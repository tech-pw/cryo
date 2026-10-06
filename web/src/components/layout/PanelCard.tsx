import type { ReactNode } from "react";
import { Card, CardContent } from "../ui/card";

interface PanelCardProps {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  quiet?: boolean;
  children: ReactNode;
}

export const PanelCard = ({ title, subtitle, actions, quiet = false, children }: PanelCardProps) => (
  <Card className={`td-chart-card${quiet ? " td-chart-card-quiet" : ""}`}>
    <CardContent className="td-chart-card-body p-0">
      <div className="td-chart-card-header">
        <span className="td-chart-card-title">{title}</span>
        {subtitle ? <span className="td-chart-card-subtitle">{subtitle}</span> : null}
        {actions ? <div className="td-chart-card-actions">{actions}</div> : null}
      </div>
      <div className="td-chart-card-content">{children}</div>
    </CardContent>
  </Card>
);
