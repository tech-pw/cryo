import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Alert } from "../ui/alert";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
import { Select } from "../ui/select";
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
  /** Filename stem used when downloading the log; defaults to "run-log". */
  downloadName?: string;
}

const entryText = (item: LogListItem): string =>
  String(item.message ?? item.status ?? item.event ?? "");

const LEVEL_OPTIONS = [
  { label: "All levels", value: "all" },
  { label: "Info", value: "info" },
  { label: "Error", value: "error" },
  { label: "Warning", value: "warning" }
];

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
  footer,
  downloadName = "run-log"
}: ConsoleShellProps) => {
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState("all");
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "error">("idle");
  const consoleAnnouncementRef = useRef({ wsOpen, wsExhausted, runTerminal });
  const [announcement, setAnnouncement] = useState("");

  const filteredItems = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return items.filter((item) => {
      if (level !== "all" && (item.level ?? "").toLowerCase() !== level) return false;
      if (!needle) return true;
      return entryText(item).toLowerCase().includes(needle) || (item.ts ?? "").toLowerCase().includes(needle);
    });
  }, [items, query, level]);

  useEffect(() => {
    const prev = consoleAnnouncementRef.current;
    let message = "";
    if (!prev.wsOpen && wsOpen) message = "Live log connected.";
    else if (prev.wsOpen && !wsOpen && !wsExhausted && !runTerminal) message = "Live log disconnected; reconnecting.";
    else if (!prev.wsExhausted && wsExhausted) message = "Live log disconnected after multiple retries; showing polled updates.";
    else if (!prev.runTerminal && runTerminal) message = "Run finished; live log stream closed.";
    if (message) setAnnouncement(message);
    consoleAnnouncementRef.current = { wsOpen, wsExhausted, runTerminal };
  }, [wsOpen, wsExhausted, runTerminal]);

  const handleCopy = () => {
    const text = filteredItems.map((item) => `${formatTime(item.ts)}\t${entryText(item)}`).join("\n");
    navigator.clipboard
      .writeText(text)
      .then(() => setCopyStatus("copied"))
      .catch(() => setCopyStatus("error"));
    window.setTimeout(() => setCopyStatus("idle"), 2000);
  };

  const handleDownload = () => {
    const text = filteredItems.map((item) => `${formatTime(item.ts)}\t${entryText(item)}`).join("\n");
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${downloadName}.txt`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const hasFilter = query.trim().length > 0 || level !== "all";

  return (
    <Card className="ops-console-shell">
      <CardHeader className="pb-2">
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {header}
        <div aria-live="polite" className="sr-only">
          {announcement}
        </div>
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
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex flex-col gap-1">
            <Label htmlFor="ops-console-search">Search log</Label>
            <Input
              id="ops-console-search"
              type="search"
              placeholder="Filter by text…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              aria-describedby="ops-console-count"
              className="w-48"
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="ops-console-level">Level</Label>
            <Select
              id="ops-console-level"
              value={level}
              onChange={(value) => setLevel(value ?? "all")}
              options={LEVEL_OPTIONS}
            />
          </div>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={handleCopy}
            disabled={filteredItems.length === 0}
          >
            {copyStatus === "copied" ? "Copied" : copyStatus === "error" ? "Copy failed" : "Copy"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={handleDownload}
            disabled={filteredItems.length === 0}
          >
            Download
          </Button>
          <span id="ops-console-count" className="text-xs text-muted-foreground" role="status">
            {hasFilter
              ? `Showing ${filteredItems.length} of ${items.length} entries`
              : `${items.length} entries`}
          </span>
        </div>
        <LogList
          title=""
          items={filteredItems}
          emptyText={hasFilter && items.length > 0 ? "No log entries match the current filter." : emptyText}
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
};
