import { type HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("animate-pulse rounded-[var(--radius-sm)] bg-[var(--color-surface-sunken)]", className)}
      {...props}
    />
  );
}

export { Skeleton };
