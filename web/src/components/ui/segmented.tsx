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
}

export function Segmented<T extends string>({
  value,
  onChange,
  options,
  size = "default",
  block,
  className,
  "aria-label": ariaLabel
}: SegmentedProps<T>) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className={cn(
        "inline-flex max-w-full overflow-x-auto rounded-[var(--radius-lg)] border border-[var(--color-control-border)] bg-background p-0.5",
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
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
