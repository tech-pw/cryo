import { cn } from "../../lib/cn";

export type ChartEventKind = "maintenance" | "schema" | "restore";

export interface ChartEvent {
  id: string;
  at: string;
  kind: ChartEventKind;
  label: string;
}

const KIND_LABEL: Record<ChartEventKind, string> = {
  maintenance: "Maintenance",
  schema: "Schema change",
  restore: "Restore"
};

export interface EventMarkersProps {
  events: readonly ChartEvent[];
  className?: string;
  onSelect?: (event: ChartEvent) => void;
}

export function EventMarkers({ events, className, onSelect }: EventMarkersProps) {
  if (!events.length) return null;
  return (
    <ul aria-label="Event markers" className={cn("flex flex-wrap gap-2", className)}>
      {events.map((event) => (
        <li key={event.id}>
          <button
            type="button"
            data-event-kind={event.kind}
            className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background px-2.5 py-1 text-xs text-foreground focus-visible:outline-none focus-visible:ring-[var(--focus-ring-width)] focus-visible:ring-[var(--focus-ring-color)]"
            onClick={() => onSelect?.(event)}
          >
            <span className="font-medium">{KIND_LABEL[event.kind]}</span>
            <time dateTime={event.at}>{event.label}</time>
          </button>
        </li>
      ))}
    </ul>
  );
}
