import { useEffect, useState, type HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("animate-pulse rounded-[var(--radius-sm)] bg-[var(--color-surface-sunken)]", className)}
      {...props}
    />
  );
}

export const skeletonStalled = (elapsedMs: number, capMs = 5000): boolean => elapsedMs >= capMs;

function LoadingSkeleton({ label = "Still loading…", timeoutMs = 5000 }: { label?: string; timeoutMs?: number }) {
  const [stalled, setStalled] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setStalled(true), timeoutMs);
    return () => window.clearTimeout(timer);
  }, [timeoutMs]);
  if (stalled) return <p>{label}</p>;
  return <Skeleton aria-busy="true" />;
}

export { LoadingSkeleton, Skeleton };
