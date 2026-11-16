import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface RadioOption {
  value: string;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
}

export interface RadioGroupProps {
  value?: string;
  onChange?: (value: string) => void;
  options: RadioOption[];
  className?: string;
  name?: string;
  disabled?: boolean;
  "aria-label"?: string;
  "aria-labelledby"?: string;
  /** "card" draws each option as a bordered, selectable tile. */
  variant?: "plain" | "card";
}

export function RadioGroup({
  value,
  onChange,
  options,
  className,
  name,
  disabled,
  variant = "plain",
  ...aria
}: RadioGroupProps) {
  const card = variant === "card";
  return (
    <div className={cn(card ? "grid gap-2 sm:grid-cols-2" : "space-y-3", className)} role="radiogroup" {...aria}>
      {options.map((option) => {
        const checked = value === option.value;
        const optionDisabled = disabled || option.disabled;
        return (
          <label
            key={option.value}
            className={cn(
              "flex gap-2",
              optionDisabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
              card &&
                "rounded-[var(--radius-default)] border border-border bg-background p-3 transition-colors duration-[var(--motion-fast)] focus-within:ring-[var(--focus-ring-width)] focus-within:ring-[var(--focus-ring-color)]",
              card && checked && "border-primary bg-[var(--color-primary-soft)]",
              card && !checked && !optionDisabled && "hover:bg-[var(--color-surface-sunken)]"
            )}
          >
            <input
              type="radio"
              name={name}
              value={option.value}
              className="mt-1"
              checked={checked}
              disabled={optionDisabled}
              onChange={() => onChange?.(option.value)}
            />
            <span className="min-w-0">
              <span className="block text-sm font-medium text-foreground">{option.label}</span>
              {option.description ? (
                <span className="mt-0.5 block text-xs text-muted-foreground">{option.description}</span>
              ) : null}
            </span>
          </label>
        );
      })}
    </div>
  );
}
