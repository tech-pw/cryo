import { Command as CommandPrimitive } from "cmdk";
import { Check, ChevronsUpDown, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { cn } from "../../lib/cn";
import { Button } from "./button";
import { Popover, PopoverContent, PopoverTrigger } from "./popover";

export interface MultiSelectOption {
  label: string;
  value: string;
  disabled?: boolean;
}

export interface MultiSelectProps {
  value?: string[];
  onChange?: (value: string[]) => void;
  options: MultiSelectOption[];
  placeholder?: string;
  className?: string;
  "aria-label"?: string;
}

/**
 * Popover + cmdk replacement for a native `<select multiple>`. The native element
 * auto-sizes to its option count and renders an always-open, OS-scrollbarred listbox --
 * this closes to a single-line trigger button and opens a filterable checklist instead.
 */
export const MultiSelect = ({
  value = [],
  onChange,
  options,
  placeholder = "Select…",
  className,
  ...rest
}: MultiSelectProps) => {
  const [open, setOpen] = useState(false);
  const selected = new Set(value);

  const toggle = (optionValue: string) => {
    const next = selected.has(optionValue)
      ? value.filter((entry) => entry !== optionValue)
      : [...value, optionValue];
    onChange?.(next);
  };

  const summary: ReactNode =
    value.length === 0
      ? placeholder
      : value.length === 1
        ? (options.find((option) => option.value === value[0])?.label ?? value[0])
        : `${placeholder}: ${value.length} selected`;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label={rest["aria-label"] ?? placeholder}
          className={cn(
            "min-w-[9rem] justify-between font-normal",
            value.length === 0 && "text-muted-foreground",
            className
          )}
        >
          <span className="truncate">{summary}</span>
          <span className="flex shrink-0 items-center gap-1">
            {value.length > 0 ? (
              <span
                role="button"
                tabIndex={-1}
                onClick={(event) => {
                  event.stopPropagation();
                  onChange?.([]);
                }}
                className="rounded-[var(--radius-sm)] p-0.5 hover:bg-[var(--color-surface-sunken)]"
                aria-label={`Clear ${placeholder}`}
              >
                <X className="h-3 w-3" aria-hidden />
              </span>
            ) : null}
            <ChevronsUpDown className="h-3.5 w-3.5 opacity-50" aria-hidden />
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-56 p-0">
        <CommandPrimitive label={placeholder}>
          <CommandPrimitive.Input
            placeholder={`Search ${placeholder.toLowerCase()}…`}
            className={cn(
              "flex h-9 w-full rounded-t-[var(--radius-default)] border-b border-border bg-transparent px-3 py-2 text-sm outline-none",
              "placeholder:text-muted-foreground"
            )}
          />
          <CommandPrimitive.List className="max-h-60 overflow-y-auto p-1">
            <CommandPrimitive.Empty className="py-4 text-center text-sm text-muted-foreground">
              No results.
            </CommandPrimitive.Empty>
            {options.map((option) => {
              const isSelected = selected.has(option.value);
              return (
                <CommandPrimitive.Item
                  key={option.value}
                  value={option.label}
                  disabled={option.disabled}
                  onSelect={() => toggle(option.value)}
                  className={cn(
                    "flex cursor-pointer items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 text-sm text-foreground",
                    "aria-selected:bg-[var(--color-surface-sunken)] data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-60"
                  )}
                >
                  <span
                    className={cn(
                      "flex h-4 w-4 shrink-0 items-center justify-center rounded-[3px] border border-input",
                      isSelected && "border-primary bg-primary text-primary-foreground"
                    )}
                    aria-hidden
                  >
                    {isSelected ? <Check className="h-3 w-3" strokeWidth={3} /> : null}
                  </span>
                  <span className="truncate">{option.label}</span>
                </CommandPrimitive.Item>
              );
            })}
          </CommandPrimitive.List>
          {value.length > 0 ? (
            <button
              type="button"
              onClick={() => onChange?.([])}
              className="w-full rounded-b-[var(--radius-default)] border-t border-border px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-[var(--color-surface-sunken)]"
            >
              Clear all
            </button>
          ) : null}
        </CommandPrimitive>
      </PopoverContent>
    </Popover>
  );
};
MultiSelect.displayName = "MultiSelect";
