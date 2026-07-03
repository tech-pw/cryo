import { Badge, Tag, type TagProps } from "antd";
import type { ReactNode } from "react";

const gradeColor: Record<string, string> = {
  A: "success",
  B: "processing",
  C: "warning",
  D: "orange",
  F: "error"
};

const priorityColor: Record<string, TagProps["color"]> = {
  HIGH: "red",
  MEDIUM: "gold",
  LOW: "default"
};

const statusColor: Record<string, TagProps["color"]> = {
  queued: "default",
  admitted: "blue",
  running: "processing",
  succeeded: "success",
  success: "success",
  failed: "error",
  cancelled: "warning",
  skipped: "default",
  deferred: "gold"
};

export const GradePill = ({ grade }: { grade?: string | null }) => (
  <Tag className="grade-pill" color={grade ? gradeColor[grade] ?? "default" : "default"}>
    {grade ?? "-"}
  </Tag>
);

export const PriorityTag = ({ priority }: { priority?: string | null }) => (
  <Tag color={priority ? priorityColor[priority] ?? "default" : "default"}>{priority ?? "-"}</Tag>
);

export const StatusTag = ({ status }: { status?: string | null }) => (
  <Tag color={status ? statusColor[status.toLowerCase()] ?? "default" : "default"}>
    {status ?? "-"}
  </Tag>
);

export const HealthBadge = ({ healthy, children }: { healthy?: boolean; children?: ReactNode }) => (
  <Badge status={healthy ? "success" : "warning"} text={children ?? (healthy ? "Healthy" : "Attention")} />
);
