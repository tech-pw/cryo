import { Archive } from "lucide-react";

import { Badge } from "./ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

export type TableLifecycle = "active" | "archived";

export const ARCHIVED_TOOLTIP =
  "Archived (read-only). The stale-commit check doesn't apply; compaction and layout changes stop. Snapshot expiry and orphan cleanup still run.";

/** Renders nothing for an active table; archived is the exception worth flagging. */
export const LifecycleBadge = ({ lifecycle, className }: { lifecycle?: TableLifecycle | null; className?: string }) => {
  if (lifecycle !== "archived") return null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant="secondary" className={className} data-testid="lifecycle-badge">
          <Archive size={12} aria-hidden="true" className="mr-1" />
          Archived
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">{ARCHIVED_TOOLTIP}</TooltipContent>
    </Tooltip>
  );
};
