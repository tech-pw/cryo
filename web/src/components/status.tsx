import { CheckCircle2, Lock } from "lucide-react";
import type { ReactNode } from "react";

import { formatRunKind } from "../lib/operations";
import { cryoColors } from "../lib/theme";
import { Badge } from "./ui/badge";
import { cn } from "../lib/cn";

const gradeVariant = (grade?: string | null) => {
  switch (grade) {
    case "A":
      return "gradeA" as const;
    case "B":
      return "gradeB" as const;
    case "C":
      return "gradeC" as const;
    case "D":
      return "gradeD" as const;
    case "F":
      return "gradeF" as const;
    default:
      return "secondary" as const;
  }
};

const priorityVariant = (priority?: string | null) => {
  switch (priority) {
    case "HIGH":
      return "danger" as const;
    case "MEDIUM":
      return "warning" as const;
    case "LOW":
      return "secondary" as const;
    default:
      return "secondary" as const;
  }
};

const statusVariant = (status?: string | null) => {
  const key = status?.toLowerCase() ?? "";
  if (["succeeded", "success"].includes(key)) return "success" as const;
  if (["failed", "error"].includes(key)) return "danger" as const;
  if (["running", "admitted", "processing"].includes(key)) return "default" as const;
  if (["cancelled", "warning", "partial_failed", "deferred"].includes(key)) return "warning" as const;
  if (["scheduled", "queued", "accepted", "skipped"].includes(key)) return "secondary" as const;
  return "secondary" as const;
};

const runKindVariant = (category: "scan" | "maintenance" | "other") => {
  if (category === "scan") return "default" as const;
  if (category === "maintenance") return "default" as const;
  return "secondary" as const;
};

export const GradePill = ({ grade }: { grade?: string | null }) => (
  <Badge variant={gradeVariant(grade)} className="grade-pill tabular-nums">
    {grade ?? "-"}
  </Badge>
);

export const PriorityTag = ({ priority }: { priority?: string | null }) => (
  <Badge variant={priorityVariant(priority)}>{priority ?? "-"}</Badge>
);

export const StatusTag = ({ status }: { status?: string | null }) => (
  <Badge variant={statusVariant(status)}>{status ?? "-"}</Badge>
);

export const RunKindTag = ({ kind }: { kind?: string | null }) => {
  const { label, category } = formatRunKind(kind);
  return <Badge variant={runKindVariant(category)}>{label}</Badge>;
};

/** Map a 0–100 health score to a chart/progress color (shared across dashboard pages). */
export const healthScoreColor = (score: number): string => {
  if (score >= 80) return cryoColors.gradeA;
  if (score >= 60) return cryoColors.gradeC;
  return cryoColors.gradeF;
};

export const CompactionTag = ({ needsCompaction }: { needsCompaction?: boolean | null }) => (
  <Badge variant={needsCompaction ? "warning" : "secondary"}>
    {needsCompaction ? "Needs compaction" : "Healthy"}
  </Badge>
);

export const HealthBadge = ({ healthy, children }: { healthy?: boolean; children?: ReactNode }) => (
  <span className="inline-flex items-center gap-1.5 text-sm">
    <span
      className={cn(
        "inline-block h-2 w-2 rounded-full",
        healthy ? "bg-[var(--color-status-success)]" : "bg-[var(--color-status-warning)]"
      )}
      aria-hidden
    />
    <span>{children ?? (healthy ? "Healthy" : "Attention")}</span>
  </span>
);

export const PendingDeletionTag = ({ pending }: { pending?: boolean }) =>
  pending ? (
    <Badge variant="warning" className="gap-1">
      <Lock className="size-3" aria-hidden />
      Pending
    </Badge>
  ) : (
    <Badge variant="success" className="gap-1">
      <CheckCircle2 className="size-3" aria-hidden />
      None
    </Badge>
  );
