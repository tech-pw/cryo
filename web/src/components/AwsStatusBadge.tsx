import { useQueries } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, XCircle } from "lucide-react";
import { getAwsIntegration } from "../api/client";
import type { UUID } from "../api/types";
import { useAuth } from "../lib/auth";
import { AwsStatusSpinner } from "./CostPrecisionBadge";
import { Badge } from "./ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

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
      <Badge variant="secondary" className="gap-1">
        <AwsStatusSpinner /> AWS
      </Badge>
    );
  }
  if (status.kind === "missing") {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge variant="secondary">AWS</Badge>
        </TooltipTrigger>
        <TooltipContent>No AWS integration — click Manage on the AWS page to connect.</TooltipContent>
      </Tooltip>
    );
  }
  if (status.kind === "error") {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge variant="danger" className="gap-1">
            <AlertCircle className="size-3" /> AWS
          </Badge>
        </TooltipTrigger>
        <TooltipContent>Could not read AWS integration for this lakehouse.</TooltipContent>
      </Tooltip>
    );
  }
  if (!status.enabled) {
    return <Badge variant="secondary">AWS · off</Badge>;
  }
  if (status.lastTestOk === false) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge variant="danger" className="gap-1">
            <XCircle className="size-3" /> AWS
          </Badge>
        </TooltipTrigger>
        <TooltipContent>Last STS test failed — credentials may be rotated or revoked.</TooltipContent>
      </Tooltip>
    );
  }
  if (status.lastTestOk === true) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge variant="success" className="gap-1">
            <CheckCircle2 className="size-3" /> AWS
          </Badge>
        </TooltipTrigger>
        <TooltipContent>AWS integration connected and verified.</TooltipContent>
      </Tooltip>
    );
  }
  return <Badge variant="default">AWS · untested</Badge>;
};
