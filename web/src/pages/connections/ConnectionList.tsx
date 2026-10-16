import type { ReactNode } from "react";
import { EmptyState } from "../../components/ui/empty-state";
import { Skeleton } from "../../components/ui/skeleton";
import { cn } from "../../lib/cn";
import type { ConnectionDetail } from "./connectionSummary";
import "./connections.css";

export const ConnectionSection = ({
  title,
  description,
  action,
  note,
  children
}: {
  title: string;
  description: ReactNode;
  action?: ReactNode;
  note?: ReactNode;
  children: ReactNode;
}) => (
  <section className="connection-section">
    <header className="connection-section__header">
      <div className="connection-section__copy">
        <h2 className="connection-section__title">{title}</h2>
        <p className="connection-section__description">{description}</p>
        {note ? <p className="connection-section__note">{note}</p> : null}
      </div>
      {action ? <div className="connection-section__action">{action}</div> : null}
    </header>
    {children}
  </section>
);

export const ConnectionGroup = ({
  title,
  description,
  children
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
}) => (
  <div className="connection-group">
    <div className="connection-group__header">
      <h3 className="connection-group__title">{title}</h3>
      {description ? <p className="connection-group__description">{description}</p> : null}
    </div>
    {children}
  </div>
);

export const ConnectionList = ({
  label,
  loading,
  emptyTitle,
  emptyDescription,
  children
}: {
  label: string;
  loading?: boolean;
  emptyTitle: string;
  emptyDescription: ReactNode;
  children: ReactNode[];
}) => {
  if (loading) {
    return (
      <div className="connection-list" aria-busy="true" aria-label={label}>
        {[0, 1].map((key) => (
          <div key={key} className="connection-row connection-row--skeleton">
            <Skeleton className="size-9 rounded-[10px]" />
            <div className="flex flex-col gap-2">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-3 w-72 max-w-full" />
            </div>
          </div>
        ))}
      </div>
    );
  }
  if (children.length === 0) {
    return <EmptyState title={emptyTitle} description={emptyDescription} />;
  }
  return (
    <ul className="connection-list" aria-label={label}>
      {children}
    </ul>
  );
};

export const ConnectionStatus = ({ enabled, label }: { enabled: boolean; label?: ReactNode }) => (
  <span className={cn("connection-status", enabled ? "connection-status--on" : "connection-status--off")}>
    <span className="connection-status__dot" aria-hidden="true" />
    {label ?? (enabled ? "Enabled" : "Disabled")}
  </span>
);

export const ConnectionRow = ({
  icon,
  name,
  kind,
  enabled,
  status,
  badges,
  details,
  actions
}: {
  icon: ReactNode;
  name: string;
  kind: ReactNode;
  enabled: boolean;
  status: ReactNode;
  badges?: ReactNode;
  details: ConnectionDetail[];
  actions: ReactNode;
}) => (
  <li className={cn("connection-row", !enabled && "connection-row--disabled")}>
    <span className="connection-row__mark" aria-hidden="true">
      {icon}
    </span>
    <div className="connection-row__main">
      <div className="connection-row__heading">
        <span className="connection-row__name">{name}</span>
        <span className="connection-row__kind">{kind}</span>
        {status}
        {badges}
      </div>
      {details.length ? (
        <dl className="connection-row__details">
          {details.map((detail) => (
            <div key={detail.label} className="connection-row__detail">
              <dt>{detail.label}</dt>
              <dd className={cn(detail.mono && "connection-row__mono")} title={detail.value}>
                {detail.value}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
    <div className="connection-row__actions">{actions}</div>
  </li>
);
