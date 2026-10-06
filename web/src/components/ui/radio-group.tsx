import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface RadioOption {
  value: string;
  label: ReactNode;
  description?: ReactNode;
}

export interface RadioGroupProps {
  value?: string;
  onChange?: (value: string) => void;
  options: RadioOption[];
  className?: string;
  name?: string;
}

export function RadioGroup({ value, onChange, options, className, name }: RadioGroupProps) {
  return (
    <div className={cn("space-y-3", className)} role="radiogroup">
      {options.map((option) => (
        <label key={option.value} className="flex cursor-pointer gap-2">
          <input
            type="radio"
            name={name}
            className="mt-1"
            checked={value === option.value}
            onChange={() => onChange?.(option.value)}
          />
          <span className="min-w-0">
            <span className="block text-sm font-medium text-foreground">{option.label}</span>
            {option.description ? (
              <span className="mt-0.5 block text-xs text-muted-foreground">{option.description}</span>
            ) : null}
          </span>
        </label>
      ))}
    </div>
  );
}
