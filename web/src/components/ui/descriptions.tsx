import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface DescriptionItem {
  label: ReactNode;
  children: ReactNode;
  span?: number;
}

export interface DescriptionsProps {
  items: DescriptionItem[];
  column?: number;
  bordered?: boolean;
  size?: "small" | "default";
  className?: string;
}

export function Descriptions({
  items,
  column = 2,
  bordered = false,
  size = "default",
  className
}: DescriptionsProps) {
  return (
    <dl
      className={cn(
        "grid gap-px overflow-hidden rounded-[var(--radius-default)] text-sm",
        bordered && "border border-border",
        size === "small" && "text-xs",
        className
      )}
      style={{ gridTemplateColumns: `repeat(${column}, minmax(0, 1fr))` }}
    >
      {items.map((item, index) => (
        <div
          key={index}
          className={cn(
            "contents",
            item.span === column ? "[&>dt]:col-span-1 [&>dd]:col-span-full" : undefined
          )}
          style={item.span && item.span > 1 ? { gridColumn: `span ${item.span}` } : undefined}
        >
          <dt
            className={cn(
              "bg-[var(--color-surface-sunken)] px-3 py-2 font-medium text-muted-foreground",
              bordered && "border-b border-border"
            )}
          >
            {item.label}
          </dt>
          <dd
            className={cn(
              "bg-background px-3 py-2 text-foreground",
              bordered && "border-b border-border"
            )}
          >
            {item.children}
          </dd>
        </div>
      ))}
    </dl>
  );
}
