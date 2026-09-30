import type { ReactNode } from "react";
import { Card } from "../../ui/card";

export const OverviewCard = ({ id, title, description, className = "", children }: {
  id: string; title: string; description?: string; className?: string; children: ReactNode;
}) => (
  <Card role="region" aria-labelledby={id} className={`td-overview-card ${className}`}>
    <header className="td-overview-card-heading">
      <h2 id={id}>{title}</h2>
      {description ? <p>{description}</p> : null}
    </header>
    {children}
  </Card>
);
