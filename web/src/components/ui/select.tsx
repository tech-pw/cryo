import { forwardRef, type SelectHTMLAttributes } from "react";
import { cn } from "../../lib/cn";

export interface SelectOption {
  label: string;
  value: string;
  disabled?: boolean;
}

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "value" | "onChange"> {
  value?: string;
  onChange?: (value: string | undefined) => void;
  options: SelectOption[];
  placeholder?: string;
  allowClear?: boolean;
  loading?: boolean;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, value, onChange, options, placeholder, allowClear, loading, disabled, ...props }, ref) => (
    <select
      ref={ref}
      className={cn(
        "cryo-select h-[var(--control-height)] min-w-[8rem] rounded-[var(--radius-default)] border border-[var(--color-control-border)] bg-background pl-3 pr-8 text-sm text-foreground",
        "focus-visible:outline-none focus-visible:ring-[var(--focus-ring-width)] focus-visible:ring-[var(--focus-ring-color)] focus-visible:ring-offset-[var(--focus-ring-offset)]",
        "disabled:cursor-not-allowed disabled:opacity-60",
        className
      )}
      value={value ?? ""}
      disabled={disabled || loading}
      onChange={(event) => {
        const next = event.target.value;
        onChange?.(next === "" ? undefined : next);
      }}
      {...props}
    >
      {allowClear || placeholder ? (
        <option value="">{placeholder ?? "Select…"}</option>
      ) : null}
      {options.map((option) => (
        <option key={option.value} value={option.value} disabled={option.disabled}>
          {option.label}
        </option>
      ))}
    </select>
  )
);
Select.displayName = "Select";

export interface MultiSelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "value" | "onChange"> {
  value?: string[];
  onChange?: (value: string[]) => void;
  options: SelectOption[];
  placeholder?: string;
}

export const MultiSelect = forwardRef<HTMLSelectElement, MultiSelectProps>(
  ({ className, value = [], onChange, options, placeholder, ...props }, ref) => (
    <select
      ref={ref}
      multiple
      className={cn(
        "min-h-[var(--control-height)] min-w-[8rem] rounded-[var(--radius-default)] border border-[var(--color-control-border)] bg-background px-2 py-1 text-sm text-foreground",
        "focus-visible:outline-none focus-visible:ring-[var(--focus-ring-width)] focus-visible:ring-[var(--focus-ring-color)] focus-visible:ring-offset-[var(--focus-ring-offset)]",
        className
      )}
      value={value}
      onChange={(event) => {
        const selected = Array.from(event.target.selectedOptions).map((option) => option.value);
        onChange?.(selected);
      }}
      aria-label={props["aria-label"] ?? placeholder}
      {...props}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value} disabled={option.disabled}>
          {option.label}
        </option>
      ))}
    </select>
  )
);
MultiSelect.displayName = "MultiSelect";
