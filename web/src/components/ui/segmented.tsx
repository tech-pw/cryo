import type { KeyboardEvent } from "react";

import { cn } from "../../lib/cn";

export interface SegmentedOption<T extends string> {
  label: string;
  value: T;
}

export interface SegmentedProps<T extends string> {
  value: T;
  onChange: (value: T) => void;
  options: SegmentedOption<T>[];
  size?: "small" | "default";
  block?: boolean;
  className?: string;
  "aria-label"?: string;
  "aria-describedby"?: string;
  /** Exposes the options as a radio group (arrow keys move the selection) instead of toggle buttons. */
  radio?: boolean;
}

export function Segmented<T extends string>({
  value,
  onChange,
  options,
  size = "default",
  block,
  className,
  "aria-label": ariaLabel,
  "aria-describedby": ariaDescribedBy,
  radio
}: SegmentedProps<T>) {
  const onRadioKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (!radio || !step) return;
    event.preventDefault();
    const current = options.findIndex((option) => option.value === value);
    const next = (current + step + options.length) % options.length;
    onChange(options[next].value);
    const buttons = event.currentTarget.querySelectorAll<HTMLButtonElement>("[role=radio]");
    buttons[next]?.focus();
  };
  return (
    <div
      role={radio ? "radiogroup" : "group"}
      onKeyDown={radio ? onRadioKeyDown : undefined}
      aria-label={ariaLabel}
      aria-describedby={ariaDescribedBy}
      className={cn(
        "cryo-segmented inline-flex max-w-full overflow-x-auto rounded-[var(--radius-lg)] border border-[var(--color-control-border)] bg-background p-0.5",
        block && "flex w-full",
        className
      )}
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={cn(
            "rounded-[var(--radius-default)] font-medium transition-colors duration-[var(--motion-fast)]",
            "focus-visible:outline-none focus-visible:ring-[var(--focus-ring-width)] focus-visible:ring-[var(--focus-ring-color)] focus-visible:ring-offset-1",
            size === "small" ? "px-2 py-1 text-xs" : "px-3 py-1.5 text-sm",
            "whitespace-nowrap",
            block && "flex-1",
            value === option.value
              ? "bg-[var(--color-primary-soft)] text-[var(--color-on-tint)]"
              : "text-muted-foreground hover:text-foreground"
          )}
          {...(radio
            ? { role: "radio", "aria-checked": value === option.value, tabIndex: value === option.value ? 0 : -1 }
            : { "aria-pressed": value === option.value })}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
