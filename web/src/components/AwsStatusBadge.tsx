import { useQueries } from "@tanstack/react-query";
import { Spin, Tag, Tooltip } from "antd";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined
} from "@ant-design/icons";
import { getAwsIntegration } from "../api/client";
import type { UUID } from "../api/types";
import { useAuth } from "../lib/auth";

export type AwsSourceStatus =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "error" }
  | {
      kind: "connected";
      enabled: boolean;
      lastTestOk: boolean | null;
      bucket: string;
      region: string;
    };

const statusOf = (
  isLoading: boolean | undefined,
  isError: boolean | undefined,
  error: unknown,
  data: { enabled: boolean; last_test_ok?: boolean | null; bucket: string; region: string } | undefined
): AwsSourceStatus => {
  if (isLoading) return { kind: "loading" };
  if (isError) {
    const status = (error as { status?: number } | null)?.status;
    return status === 404 ? { kind: "missing" } : { kind: "error" };
  }
  if (!data) return { kind: "missing" };
  return {
    kind: "connected",
    enabled: data.enabled,
    lastTestOk: data.last_test_ok ?? null,
    bucket: data.bucket,
    region: data.region
  };
};

/** Resolve AWS integration status for a batch of source IDs in parallel. */
export const useAwsSourceStatuses = (sourceIds: UUID[]) => {
  const { api, token } = useAuth();
  const results = useQueries({
    queries: sourceIds.map((id) => ({
      queryKey: ["aws-integration-badge", id, token],
      queryFn: () => getAwsIntegration(api, id),
      retry: false,
      refetchOnWindowFocus: false,
      staleTime: 30_000
    }))
  });
  const map: Record<UUID, AwsSourceStatus> = {};
  sourceIds.forEach((id, i) => {
    const r = results[i];
    map[id] = statusOf(r?.isLoading, r?.isError, r?.error, r?.data);
  });
  return map;
};

export const AwsStatusBadge = ({ status }: { status: AwsSourceStatus | undefined }) => {
  if (!status || status.kind === "loading") {
    return (
      <Tag color="default">
        <Spin size="small" style={{ marginRight: 4 }} /> AWS
      </Tag>
    );
  }
  if (status.kind === "missing") {
    return (
      <Tooltip title="No AWS integration — click Manage on the AWS page to connect.">
        <Tag>AWS</Tag>
      </Tooltip>
    );
  }
  if (status.kind === "error") {
    return (
      <Tooltip title="Could not read AWS integration for this lakehouse.">
        <Tag icon={<ExclamationCircleOutlined />} color="red">
          AWS
        </Tag>
      </Tooltip>
    );
  }
  if (!status.enabled) {
    return <Tag color="default">AWS · off</Tag>;
  }
  if (status.lastTestOk === false) {
    return (
      <Tooltip title="Last STS test failed — credentials may be rotated or revoked.">
        <Tag icon={<CloseCircleOutlined />} color="red">
          AWS
        </Tag>
      </Tooltip>
    );
  }
  if (status.lastTestOk === true) {
    return (
      <Tooltip title="AWS integration connected and verified.">
        <Tag icon={<CheckCircleOutlined />} color="green">
          AWS
        </Tag>
      </Tooltip>
    );
  }
  return <Tag color="processing">AWS · untested</Tag>;
};