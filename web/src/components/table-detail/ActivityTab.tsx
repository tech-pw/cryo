import type { AuditEntry } from "../../api/types";
import { EmptyState } from "../common";
import { Badge } from "../ui/badge";
import { actionIcon } from "./helpers";
import { ChartCard } from "./ChartCard";
import { formatBytesGb, formatDuration, formatTime } from "../../lib/format";

const BYTE_ACTIONS = new Set([
  "optimize",
  "remove_orphan_files",
  "convert_deletes",
  "rewrite_position_deletes"
]);

const byteLabel = (item: AuditEntry) => {
  if (!BYTE_ACTIONS.has(item.action ?? "")) return null;
  if (item.actual_bytes_freed == null) return <> · Bytes unknown</>;
  return <> · Freed {formatBytesGb(item.actual_bytes_freed)}</>;
};

interface ActivityTabProps {
  entries: AuditEntry[] | undefined;
}

export const ActivityTab = ({ entries }: ActivityTabProps) => {
  const list = (entries ?? []).slice(0, 12);
  return (
    <div className="td-tab-content">
      <ChartCard
        title="Recent Activity"
        subtitle={entries?.length ? `Last ${Math.min(entries.length, 12)} events` : undefined}
      >
        {list.length ? (
          <div className="td-timeline td-timeline-wide">
            {list.map((item: AuditEntry, index) => {
              const failed = item.status === "failed";
              return (
                <div
                  key={item.id ?? index}
                  className={`td-timeline-item ${failed ? "td-timeline-failed" : ""}`}
                >
                  <div className="td-timeline-dot-wrap">
                    <span
                      className={`td-timeline-dot ${failed ? "td-timeline-dot-fail" : "td-timeline-dot-ok"}`}
                    />
                    {index < list.length - 1 ? <span className="td-timeline-line" /> : null}
                  </div>
                  <div className="td-timeline-content">
                    <div className="td-timeline-head">
                      <span className="td-timeline-icon">
                        {actionIcon(item.action ?? "maintenance", failed)}
                      </span>
                      <span className="td-timeline-action">{item.action ?? "maintenance"}</span>
                      {failed ? (
                        <Badge variant="danger" className="px-1 py-0 text-[10px] leading-4">
                          Failed
                        </Badge>
                      ) : null}
                    </div>
                    <div className="td-timeline-detail">
                      {formatTime(item.ts ?? item.created_at)}
                      {item.engine ? <> · {item.engine}</> : null}
                      {item.duration_ms ? <> · {formatDuration(item.duration_ms)}</> : null}
                      {item.input_files_count != null && item.output_files_count != null
                        ? <> · {item.input_files_count} → {item.output_files_count} files</>
                        : null}
                      {byteLabel(item)}
                      {item.effect_status ? <> · {item.effect_status.replaceAll("_", " ")}</> : null}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <EmptyState
            title="No audit entries"
            description="Completed maintenance actions will appear here."
          />
        )}
      </ChartCard>
    </div>
  );
};

export default ActivityTab;
