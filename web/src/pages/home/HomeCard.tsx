import type { ReactNode } from "react";
import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";
import { Card } from "../../components/ui/card";

export const HomeCard = ({ title, description, action, children, className = "", busy, id }: {
  title: string;
  description: string;
  action?: { label: string; href: string };
  children: ReactNode;
  className?: string;
  busy?: boolean;
  id?: string;
}) => (
  <Card id={id} className={`home-card ${className}`} role="region" aria-label={title} aria-busy={busy || undefined}>
    <div className="home-card-heading">
      <div><h2>{title}</h2><p>{description}</p></div>
      {action ? <Link className="home-text-link" to={action.href}>{action.label}<ArrowUpRight size={14} aria-hidden="true" /></Link> : null}
    </div>
    {children}
  </Card>
);
