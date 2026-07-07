import { Alert, Button, Skeleton, Space } from "antd";
import type { ReactNode } from "react";

interface QueryStateProps<T> {
  isLoading?: boolean;
  isError?: boolean;
  error?: unknown;
  isEmpty?: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
  emptyAction?: ReactNode;
  onRetry?: () => void;
  children: (data: T) => ReactNode;
  data?: T;
}

const errorMessage = (err: unknown): string => {
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  return "Unexpected error";
};

export function QueryState<T>({
  isLoading,
  isError,
  error,
  isEmpty,
  emptyTitle = "Nothing here yet",
  emptyDescription,
  emptyAction,
  onRetry,
  children,
  data
}: QueryStateProps<T>) {
  if (isLoading) {
    return (
      <div role="status" aria-label="Loading">
        <Skeleton active paragraph={{ rows: 6 }} />
      </div>
    );
  }
  if (isError) {
    return (
      <Alert
        role="alert"
        type="error"
        showIcon
        message="Failed to load"
        description={errorMessage(error)}
        action={
          onRetry ? (
            <Button size="small" onClick={onRetry}>
              Retry
            </Button>
          ) : undefined
        }
      />
    );
  }
  if (isEmpty || data == null) {
    return (
      <Alert
        type="info"
        showIcon
        message={emptyTitle}
        description={
          emptyDescription ? (
            <Space direction="vertical" size={8} style={{ marginTop: 4 }}>
              <span>{emptyDescription}</span>
              {emptyAction}
            </Space>
          ) : undefined
        }
      />
    );
  }
  return <>{children(data)}</>;
}