import { AlertCircle, AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { type ReactNode } from "react";

import { cn } from "../../lib/cn";

type AlertVariant = "error" | "warning" | "info" | "success";

export interface AlertProps {
  variant?: AlertVariant;
  title: string;
  description?: ReactNode;
  className?: string;
  onDismiss?: () => void;
}

const variantStyles: Record<AlertVariant, string> = {
  error: "border-[color:var(--color-status-danger)]/30 bg-[color:var(--color-status-danger)]/10",
  warning: "border-[color:var(--color-status-warning)]/30 bg-[color:var(--color-status-warning)]/10",
  info: "border-border bg-[var(--color-surface-sunken)]",
  success: "border-[color:var(--color-status-success)]/30 bg-[color:var(--color-status-success)]/10"
};

const icons: Record<AlertVariant, typeof Info> = {
  error: AlertCircle,
  warning: AlertTriangle,
  info: Info,
  success: CheckCircle2
};

export function Alert({ variant = "info", title, description, className, onDismiss }: AlertProps) {
  const Icon = icons[variant];

  return (
    <div
      role="alert"
      className={cn(
        "relative flex gap-3 rounded-[var(--radius-default)] border p-3 text-sm text-foreground",
        variantStyles[variant],
        className
      )}
    >
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5 pr-6">
        <p className="font-medium">{title}</p>
        {description ? <div className="text-muted-foreground">{description}</div> : null}
      </div>
      {onDismiss ? (
        <button
          type="button"
          onClick={onDismiss}
          className="absolute right-2 top-2 rounded-[var(--radius-sm)] p-1 text-muted-foreground hover:bg-background/60 hover:text-foreground"
          aria-label="Dismiss"
        >
          <X className="size-3.5" />
        </button>
      ) : null}
    </div>
  );
}
