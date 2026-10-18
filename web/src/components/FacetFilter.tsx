import { ChevronDown, Search, X } from "lucide-react";
import { useMemo, useState } from "react";
import { cn } from "../lib/cn";
import { Checkbox } from "./ui/checkbox";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";

export interface FacetOption {
  label: string;
  value: string;
  hint?: string;
}

const SEARCH_THRESHOLD = 8;
const MAX_TRIGGER_LABELS = 2;

/**
 * One filter dimension as a compact trigger: "Grade" when empty, "Grade: D, F" when set. Values
 * stay visible on the bar itself, so the page needs no separate chip row for them.
 */
export const FacetFilter = ({
  label,
  options,
  value,
  onChange,
  ariaLabel
}: {
  label: string;
  options: FacetOption[];
  value: string[];
  onChange: (next: string[]) => void;
  ariaLabel?: string;
}) => {
  const [query, setQuery] = useState("");
  const selected = new Set(value);
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return needle ? options.filter((option) => option.label.toLowerCase().includes(needle)) : options;
  }, [options, query]);
  const selectedLabels = options.filter((option) => selected.has(option.value)).map((option) => option.label);
  const summary =
    selectedLabels.length > MAX_TRIGGER_LABELS
      ? `${selectedLabels.length} selected`
      : selectedLabels.join(", ");
  const toggle = (optionValue: string, checked: boolean) => {
    const next = new Set(selected);
    if (checked) next.add(optionValue);
    else next.delete(optionValue);
    onChange(options.map((option) => option.value).filter((candidate) => next.has(candidate)));
  };

  return (
    <Popover onOpenChange={(open) => !open && setQuery("")}>
      <div
        className={cn(
          "inline-flex h-[var(--control-height-sm)] items-center rounded-[var(--radius-default)] border text-xs",
          value.length
            ? "border-[var(--color-primary)] bg-[var(--color-primary-soft)] text-[var(--color-on-tint)]"
            : "border-dashed border-[var(--color-border-strong)] text-foreground"
        )}
      >
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label={ariaLabel ?? `Filter by ${label.toLowerCase()}`}
            className="inline-flex h-full cursor-pointer items-center gap-1 px-2.5 font-medium"
          >
            {label}
            {summary ? <span className="max-w-[12rem] truncate font-normal">: {summary}</span> : null}
            {value.length ? null : <ChevronDown className="size-3 opacity-60" aria-hidden="true" />}
          </button>
        </PopoverTrigger>
        {value.length ? (
          <button
            type="button"
            aria-label={`Clear ${label.toLowerCase()} filter`}
            className="inline-flex h-full cursor-pointer items-center border-l border-[color-mix(in_srgb,var(--color-primary)_35%,transparent)] px-1.5 hover:bg-[color-mix(in_srgb,var(--color-primary)_14%,transparent)]"
            onClick={() => onChange([])}
          >
            <X className="size-3" aria-hidden="true" />
          </button>
        ) : null}
      </div>
      <PopoverContent align="start" className="w-60 p-1.5">
        {options.length > SEARCH_THRESHOLD ? (
          <div className="mb-1 flex items-center gap-1.5 border-b border-border px-1.5 pb-1.5">
            <Search className="size-3.5 text-muted-foreground" aria-hidden="true" />
            <input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={`Search ${label.toLowerCase()}`}
              aria-label={`Search ${label.toLowerCase()} options`}
              className="h-7 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
          </div>
        ) : null}
        <ul className="flex max-h-64 flex-col gap-0.5 overflow-y-auto" aria-label={`${label} options`}>
          {visible.map((option) => (
            <li key={option.value}>
              <label className="flex cursor-pointer items-center gap-2 rounded-[var(--radius-sm)] px-1.5 py-1.5 text-sm hover:bg-[var(--color-surface-sunken)]">
                <Checkbox
                  checked={selected.has(option.value)}
                  onCheckedChange={(checked) => toggle(option.value, checked === true)}
                />
                <span className="min-w-0 flex-1 truncate">{option.label}</span>
                {option.hint ? <span className="text-xs text-muted-foreground">{option.hint}</span> : null}
              </label>
            </li>
          ))}
          {visible.length === 0 ? <li className="px-1.5 py-2 text-sm text-muted-foreground">No matches</li> : null}
        </ul>
        {value.length ? (
          <div className="mt-1 border-t border-border pt-1">
            <button
              type="button"
              className="w-full cursor-pointer rounded-[var(--radius-sm)] px-1.5 py-1 text-left text-xs text-muted-foreground hover:bg-[var(--color-surface-sunken)] hover:text-foreground"
              onClick={() => onChange([])}
            >
              Clear selection
            </button>
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  );
};
