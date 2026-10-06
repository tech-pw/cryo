import { useEffect, useRef } from "react";

import { cn } from "../lib/cn";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

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
  runTerminal?: boolean;
  reconnectAttempt?: number;
  onManualReconnect?: () => void;
  formatTime: (ts?: string) => string;
}

const connectionDotClass = (
  runTerminal: boolean,
  wsExhausted: boolean,
  wsOpen: boolean
): string => {
  if (runTerminal) return "bg-muted-foreground";
  if (wsExhausted) return "bg-[var(--color-status-danger)]";
  if (wsOpen) return "bg-[var(--color-status-success)]";
  return "bg-[var(--color-primary)] animate-pulse";
};

export const LogList = ({
  title,
  items,
  emptyText,
  wsOpen,
  wsExhausted = false,
  runTerminal = false,
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

  const connectionLabel = runTerminal
    ? "Ended"
    : wsExhausted
      ? `Disconnected (retry ${reconnectAttempt})`
      : wsOpen
        ? "Live"
        : `Reconnecting (${reconnectAttempt})`;
  const connectionTooltip = runTerminal
    ? "Run finished; live log stream closed."
    : wsExhausted
      ? "Lost connection to live log stream. Click to retry."
      : wsOpen
        ? "Connected to live log stream."
        : "Connecting to live log stream.";

  return (
    <div className="log-list">
      <div className="log-list-header">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold">{title}</span>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                <span
                  className={cn(
                    "inline-block h-2 w-2 rounded-full",
                    connectionDotClass(runTerminal, wsExhausted, wsOpen)
                  )}
                  aria-hidden
                />
                <span
                  className={wsExhausted && onManualReconnect ? "log-list-reconnect" : undefined}
                  onClick={wsExhausted && onManualReconnect ? onManualReconnect : undefined}
                  role={wsExhausted && onManualReconnect ? "button" : undefined}
                >
                  {connectionLabel}
                </span>
              </span>
            </TooltipTrigger>
            <TooltipContent>{connectionTooltip}</TooltipContent>
          </Tooltip>
        </div>
      </div>
      <div ref={scrollRef} className="log-list-body">
        {items.length === 0 ? <div className="log-list-empty">{emptyText}</div> : null}
        {items.length > 0 ? (
          <ul className="log-list-ul">
            {items.map((item, idx) => (
              <li key={`${item.ts ?? ""}-${idx}`} className="log-list-item">
                <code className="font-mono text-xs text-muted-foreground">{formatTime(item.ts)}</code>
                <span
                  className={cn(
                    "text-sm",
                    item.level === "error" && "text-[var(--color-status-danger)]"
                  )}
                >
                  {item.message ?? item.status ?? item.event ?? ""}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
};
