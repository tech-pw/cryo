import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "../../lib/cn";
import { Input } from "./input";

export interface InputNumberProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type"> {
  value?: number | null;
  onChange?: (value: number | null) => void;
  min?: number;
  max?: number;
  addonBefore?: string;
}

export const InputNumber = forwardRef<HTMLInputElement, InputNumberProps>(
  ({ className, value, onChange, min, max, addonBefore, style, ...props }, ref) => {
    const field = (
      <Input
        ref={ref}
        type="number"
        className={cn(addonBefore ? "rounded-l-none" : undefined, className)}
        style={style}
        value={value ?? ""}
        min={min}
        max={max}
        onChange={(event) => {
          const raw = event.target.value;
          if (raw === "") {
            onChange?.(null);
            return;
          }
          const parsed = Number(raw);
          onChange?.(Number.isNaN(parsed) ? null : parsed);
        }}
        {...props}
      />
    );

    if (!addonBefore) return field;

    return (
      <div className="inline-flex w-full items-stretch">
        <span className="inline-flex items-center rounded-l-[var(--radius-default)] border border-r-0 border-border bg-[var(--color-surface-sunken)] px-2 text-xs text-muted-foreground">
          {addonBefore}
        </span>
        {field}
      </div>
    );
  }
);
InputNumber.displayName = "InputNumber";
