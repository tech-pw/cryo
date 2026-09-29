import { X } from "lucide-react";
import { formatAssistantCell } from "../../api/assistant";

const HIDDEN = new Set(["route"]);

const labelFor = (key: string): string => {
  if (key === "table_id") return "Table";
  if (key === "run_id") return "Run";
  if (key === "tab") return "Tab";
  return key.replaceAll("_", " ");
};

export const ContextChips = ({
  context,
  onRemove
}: {
  context: Record<string, unknown>;
  onRemove: (key: string) => void;
}) => {
  const chips = Object.entries(context).filter(([key, value]) => !HIDDEN.has(key) && formatAssistantCell(value) !== "-");
  if (chips.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Question context">
      {chips.map(([key, value]) => (
        <li key={key}>
          <button
            type="button"
            className="inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-[var(--color-surface-sunken)] px-2 py-0.5 text-xs text-foreground"
            onClick={() => onRemove(key)}
          >
            <span className="truncate">
              {labelFor(key)} {formatAssistantCell(value)}
            </span>
            <X className="size-3 shrink-0" aria-hidden="true" />
            <span className="sr-only">Remove {labelFor(key)}</span>
          </button>
        </li>
      ))}
    </ul>
  );
};
