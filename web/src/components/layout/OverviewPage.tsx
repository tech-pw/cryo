import type { ReactNode } from "react";
import { PageHeader, QueryErrorAlert } from "../common";
import { Skeleton } from "../ui/skeleton";

interface OverviewPageProps {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  loading?: boolean;
  error?: unknown;
  errorTitle?: string;
  preContent?: ReactNode;
  kpis?: ReactNode;
  children: ReactNode;
}

export const OverviewPage = ({
  title,
  subtitle,
  actions,
  loading,
  error,
  errorTitle,
  preContent,
  kpis,
  children
}: OverviewPageProps) => (
  <div>
    <PageHeader title={title} subtitle={subtitle} actions={actions} />
    <QueryErrorAlert error={error} title={errorTitle ?? "Failed to load page data"} />
    {loading ? (
      <Skeleton className="mt-4 h-40 w-full" />
    ) : (
      <div className="flex w-full flex-col gap-3">
        {preContent}
        {kpis}
        {children}
      </div>
    )}
  </div>
);
