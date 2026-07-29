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
}

export function Segmented<T extends string>({
  value,
  onChange,
  options,
  size = "default",
  block,
  className
}: SegmentedProps<T>) {
  return (
    <div
      role="group"
      className={cn(
        "inline-flex rounded-[var(--radius-default)] border border-border bg-[var(--color-surface-sunken)] p-0.5",
        block && "flex w-full",
        className
      )}
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={cn(
            "rounded-[var(--radius-sm)] font-medium text-foreground transition-colors duration-[var(--motion-fast)]",
            size === "small" ? "px-2 py-1 text-xs" : "px-3 py-1.5 text-sm",
            block && "flex-1",
            value === option.value
              ? "bg-background shadow-sm"
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
