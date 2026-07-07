import { Badge, Space, Tooltip, Typography } from "antd";
import { useEffect, useRef } from "react";

export interface LogListItem {
  type?: string;
  ts?: string;
  level?: string;
  message?: string;
  status?: string;
  event?: string;
  [key: string]: unknown;
}

export interface LogListProps {
  title: string;
  items: LogListItem[];
  emptyText: string;
  wsOpen: boolean;
  wsExhausted?: boolean;
  reconnectAttempt?: number;
  onManualReconnect?: () => void;
  formatTime: (ts?: string) => string;
}

export const LogList = ({
  title,
  items,
  emptyText,
  wsOpen,
  wsExhausted = false,
  reconnectAttempt = 0,
  onManualReconnect,
  formatTime
}: LogListProps) => {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const isNearBottomRef = useRef(true);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      isNearBottomRef.current = distance < 32;
    };
    el.addEventListener("scroll", onScroll);
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    if (!isNearBottomRef.current) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [items]);

  const connectionLabel = wsExhausted
    ? `Disconnected (retry ${reconnectAttempt})`
    : wsOpen
      ? "Live"
      : `Reconnecting (${reconnectAttempt})`;
  const connectionStatus: "success" | "processing" | "error" = wsExhausted
    ? "error"
    : wsOpen
      ? "success"
      : "processing";

  return (
    <div className="log-list">
      <div className="log-list-header">
        <Space size={8}>
          <Typography.Text strong>{title}</Typography.Text>
          <Tooltip
            title={
              wsExhausted
                ? "Lost connection to live log stream. Click to retry."
                : wsOpen
                  ? "Connected to live log stream."
                  : "Connecting to live log stream."
            }
          >
            <Badge
              status={connectionStatus}
              text={
                <span
                  className={wsExhausted && onManualReconnect ? "log-list-reconnect" : undefined}
                  onClick={wsExhausted && onManualReconnect ? onManualReconnect : undefined}
                  role={wsExhausted && onManualReconnect ? "button" : undefined}
                >
                  {connectionLabel}
                </span>
              }
            />
          </Tooltip>
        </Space>
      </div>
      <div ref={scrollRef} className="log-list-body">
        {items.length === 0 ? <div className="log-list-empty">{emptyText}</div> : null}
        {items.length > 0 ? (
          <ul className="log-list-ul">
            {items.map((item, idx) => (
              <li key={`${item.ts ?? ""}-${idx}`} className="log-list-item">
                <Typography.Text code>{formatTime(item.ts)}</Typography.Text>
                <Typography.Text type={item.level === "error" ? "danger" : undefined}>
                  {item.message ?? item.status ?? item.event ?? ""}
                </Typography.Text>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
};