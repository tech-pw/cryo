import { type ReactNode, useEffect, useState } from "react";
import { Button } from "./ui/button";
import { EmptyState } from "./ui/empty-state";
import { Skeleton } from "./ui/skeleton";
import { cn } from "../lib/cn";

const errorMessage = (err: unknown): string => {
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  return "Unexpected error";
};

export interface AsyncBoundaryProps {
  isLoading?: boolean;
  isError?: boolean;
  error?: unknown;
  isEmpty?: boolean;
  skeleton?: ReactNode;
  empty?: ReactNode;
  emptyTitle?: string;
  emptyDescription?: string;
  emptyAction?: ReactNode;
  onRetry?: () => void;
  children: ReactNode;
  className?: string;
}

export function AsyncBoundary({
  isLoading = false,
  isError = false,
  error,
  isEmpty = false,
  skeleton,
  empty,
  emptyTitle = "Nothing here yet",
  emptyDescription,
  emptyAction,
  onRetry,
  children,
  className,
  fade = true
}: AsyncBoundaryProps & { fade?: boolean }) {
  const [showContent, setShowContent] = useState(!isLoading);
  const [contentReady, setContentReady] = useState(!isLoading);

  useEffect(() => {
    if (isLoading) {
      setShowContent(false);
      setContentReady(false);
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof window.setTimeout> | undefined;
    const frame = requestAnimationFrame(() => {
      if (cancelled) return;
      setContentReady(true);
      timer = window.setTimeout(() => {
        if (!cancelled) setShowContent(true);
      }, 16);
    });
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [isLoading]);

  if (isLoading) {
    return (
      <div role="status" aria-label="Loading" aria-busy="true" className={className}>
        {skeleton ?? <DefaultSkeleton />}
      </div>
    );
  }

  if (isError) {
    return (
      <div role="alert" className={cn("rounded-[var(--radius-default)] border border-border bg-card p-4", className)}>
        <p className="text-sm font-medium text-foreground">Failed to load</p>
        <p className="mt-1 text-sm text-muted-foreground">{errorMessage(error)}</p>
        {onRetry ? (
          <Button variant="secondary" size="sm" className="mt-3" onClick={onRetry}>
            Retry
          </Button>
        ) : null}
      </div>
    );
  }

  if (isEmpty) {
    return (
      <div className={className}>
        {empty ?? (
          <EmptyState title={emptyTitle} description={emptyDescription} action={emptyAction} />
        )}
      </div>
    );
  }

  if (!fade) {
    return <div className={className}>{children}</div>;
  }

  return (
    <div
      className={cn(
        "transition-opacity duration-[var(--motion-base)] ease-[var(--ease-out)]",
        contentReady && showContent ? "opacity-100" : "opacity-0",
        className
      )}
      aria-busy={!showContent || undefined}
    >
      {children}
    </div>
  );
}

function DefaultSkeleton() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-4 w-1/3" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-5/6" />
      <Skeleton className="h-4 w-2/3" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-4/5" />
    </div>
  );
}
