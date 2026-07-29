import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface BreadcrumbItem {
  title: ReactNode;
}

export interface BreadcrumbProps {
  items: BreadcrumbItem[];
  className?: string;
  "aria-label"?: string;
}

export function Breadcrumb({ items, className, "aria-label": ariaLabel = "Breadcrumb" }: BreadcrumbProps) {
  if (!items.length) return null;
  return (
    <nav aria-label={ariaLabel} className={cn("mb-3 text-xs text-muted-foreground", className)}>
      <ol className="flex flex-wrap items-center gap-1.5">
        {items.map((item, index) => (
          <li key={index} className="inline-flex items-center gap-1.5">
            {index > 0 ? <ChevronRight className="size-3 opacity-60" aria-hidden /> : null}
            <span>{item.title}</span>
          </li>
        ))}
      </ol>
    </nav>
  );
}
