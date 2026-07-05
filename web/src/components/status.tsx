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
  accepted: "default",
  queued: "default",
  admitted: "blue",
  running: "processing",
  succeeded: "success",
  success: "success",
  partial_failed: "orange",
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

/** Map a 0–100 health score to a chart/progress color (shared across dashboard pages). */
export const healthScoreColor = (score: number): string => {
  if (score >= 80) return "#52c41a";
  if (score >= 60) return "#faad14";
  return "#ff4d4f";
};

export const CompactionTag = ({ needsCompaction }: { needsCompaction?: boolean | null }) => (
  <Tag color={needsCompaction ? "gold" : "default"}>
    {needsCompaction ? "Needs compaction" : "Healthy"}
  </Tag>
);

export const HealthBadge = ({ healthy, children }: { healthy?: boolean; children?: ReactNode }) => (
  <Badge status={healthy ? "success" : "warning"} text={children ?? (healthy ? "Healthy" : "Attention")} />
);
