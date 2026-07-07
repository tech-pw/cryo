import { HistoryOutlined } from "@ant-design/icons";
import { Card, Tag } from "antd";
import type { AuditEntry } from "../../api/types";
import { EmptyState } from "../common";
import { actionIcon } from "./helpers";
import { formatDuration, formatTime } from "../../lib/format";

interface ActivityTabProps {
  entries: AuditEntry[] | undefined;
}

export const ActivityTab = ({ entries }: ActivityTabProps) => {
  const list = (entries ?? []).slice(0, 12);
  return (
    <div className="td-tab-content">
      <Card className="td-chart-card" classNames={{ body: "td-chart-card-body" }}>
        <div className="td-chart-card-header">
          <span className="td-chart-card-title">Recent activity</span>
          <span className="td-chart-card-subtitle">
            {entries?.length ? `Last ${Math.min(entries.length, 12)} events` : null}
          </span>
        </div>
        <div className="td-chart-card-content">
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
                          <Tag
                            color="error"
                            style={{ fontSize: 10, lineHeight: "16px", padding: "0 4px", margin: 0 }}
                          >
                            failed
                          </Tag>
                        ) : null}
                      </div>
                      <div className="td-timeline-detail">
                        {formatTime(item.ts ?? item.created_at)}
                        {item.engine ? <> · {item.engine}</> : null}
                        {item.duration_ms ? <> · {formatDuration(item.duration_ms)}</> : null}
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
        </div>
      </Card>
    </div>
  );
};

export default ActivityTab;