import { AlertCircle } from "lucide-react";
import { useEffect, useRef } from "react";
import type { FieldErrors, FieldValues } from "react-hook-form";
import { useFormContext } from "react-hook-form";

import { cn } from "../../lib/cn";
import { formFieldId } from "./form";

export interface FormErrorEntry {
  /** RHF field path, e.g. `"name"` or `"coordinators.0.host"`. */
  path: string;
  message: string;
}

/**
 * Flattens a react-hook-form `errors` object (which nests per array/object field) into a flat
 * list of `{ path, message }` leaves, skipping RHF's own metadata keys (`type`, `ref`, `types`,
 * `root`). Exported for targeted unit tests independent of rendering.
 */
export const collectFormErrors = (errors: FieldErrors, prefix = ""): FormErrorEntry[] => {
  const entries: FormErrorEntry[] = [];
  for (const key of Object.keys(errors)) {
    if (key === "root") continue;
    const value = (errors as Record<string, unknown>)[key];
    if (!value || typeof value !== "object") continue;
    const path = prefix ? `${prefix}.${key}` : key;
    const node = value as { message?: unknown; type?: unknown };
    if (typeof node.message === "string" && node.message.length > 0) {
      entries.push({ path, message: node.message });
      continue;
    }
    if (node.type === undefined) {
      // Nested object/array of errors (e.g. a field array) — recurse one level down.
      entries.push(...collectFormErrors(value as FieldErrors, path));
    }
  }
  return entries;
};

export interface FormErrorSummaryProps<TFieldValues extends FieldValues = FieldValues> {
  /** Optional human labels for field paths, e.g. `{ source_id: "Source" }`. Falls back to the
   * raw field path when a label isn't provided. */
  labels?: Partial<Record<string, string>>;
  title?: string;
  className?: string;
  /** Focus the summary itself when errors first appear (e.g. after a failed submit). */
  autoFocus?: boolean;
}

/**
 * Focused, linked error summary for a react-hook-form form. Lists every current validation
 * error with a button that focuses and scrolls to its field — pair with `FormField`, which gives
 * every control a stable id/aria-describedby that this component's links target.
 */
export function FormErrorSummary<TFieldValues extends FieldValues = FieldValues>({
  labels,
  title = "Fix the following before continuing",
  className,
  autoFocus = true
}: FormErrorSummaryProps<TFieldValues>) {
  const {
    formState: { errors },
    setFocus
  } = useFormContext<TFieldValues>();
  const summaryRef = useRef<HTMLDivElement>(null);
  const entries = collectFormErrors(errors as FieldErrors);

  useEffect(() => {
    if (autoFocus && entries.length > 0) {
      summaryRef.current?.focus();
    }
    // Only re-run when the error count/identity actually changes, not on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries.length]);

  if (entries.length === 0) return null;

  const focusField = (path: string) => {
    try {
      setFocus(path as never);
    } catch {
      // Field may not be registered with a focusable ref (e.g. a plain <select>); fall back to
      // scrolling its labelled control into view below.
    }
    document.getElementById(formFieldId(path))?.scrollIntoView?.({ block: "center", behavior: "smooth" });
  };

  return (
    <div
      ref={summaryRef}
      role="alert"
      tabIndex={-1}
      className={cn(
        "flex flex-col gap-2 rounded-[var(--radius-default)] border p-3 text-sm",
        "border-[color:var(--color-status-danger)]/30 bg-[color:var(--color-status-danger)]/10",
        "focus-visible:outline-none focus-visible:ring-[var(--focus-ring-width)] focus-visible:ring-ring focus-visible:ring-offset-[var(--focus-ring-offset)]",
        className
      )}
    >
      <p className="flex items-center gap-2 font-medium text-foreground">
        <AlertCircle className="size-4 shrink-0" aria-hidden="true" />
        {title}
      </p>
      <ul className="ml-6 list-disc space-y-1">
        {entries.map((entry) => (
          <li key={entry.path}>
            <button
              type="button"
              className="text-left text-destructive underline underline-offset-2 hover:no-underline"
              onClick={() => focusField(entry.path)}
            >
              {labels?.[entry.path] ?? entry.path}: {entry.message}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
