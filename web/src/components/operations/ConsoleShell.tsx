import type { ReactNode } from "react";
import { Alert } from "../ui/alert";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { LogList, type LogListItem } from "../LogList";

export interface ConsoleShellProps {
  title?: string;
  items: LogListItem[];
  emptyText?: string;
  wsOpen: boolean;
  wsExhausted?: boolean;
  runTerminal?: boolean;
  reconnectAttempt?: number;
  onManualReconnect?: () => void;
  formatTime: (ts?: string) => string;
  header?: ReactNode;
  footer?: ReactNode;
}

export const ConsoleShell = ({
  title = "Live console",
  items,
  emptyText = "No live events yet.",
  wsOpen,
  wsExhausted = false,
  runTerminal = false,
  reconnectAttempt = 0,
  onManualReconnect,
  formatTime,
  header,
  footer
}: ConsoleShellProps) => (
  <Card className="ops-console-shell">
    <CardHeader className="pb-2">
      <CardTitle>{title}</CardTitle>
    </CardHeader>
    <CardContent className="space-y-3">
      {header}
      {!wsOpen && !runTerminal ? (
        <Alert
          variant={wsExhausted ? "warning" : "info"}
          className="ops-console-shell__alert"
          title={
            wsExhausted
              ? "Live socket disconnected after multiple retries; polling run state."
              : reconnectAttempt > 0
                ? `Live socket unavailable; reconnecting (attempt ${reconnectAttempt}/8)…`
                : "Live socket unavailable; polling run state."
          }
          description={
            onManualReconnect ? (
              <Button size="sm" variant="secondary" onClick={onManualReconnect}>
                Reconnect
              </Button>
            ) : undefined
          }
        />
      ) : null}
      <LogList
        title=""
        items={items}
        emptyText={emptyText}
        wsOpen={wsOpen}
        wsExhausted={wsExhausted}
        runTerminal={runTerminal}
        reconnectAttempt={reconnectAttempt}
        onManualReconnect={onManualReconnect}
        formatTime={formatTime}
      />
      {footer}
    </CardContent>
  </Card>
);
