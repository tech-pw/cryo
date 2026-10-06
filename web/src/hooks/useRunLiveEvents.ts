import { useCallback, useEffect, useRef, useState } from "react";
import { wsRunUrl } from "../api/client";
import type { JobRead, RunRead, UUID } from "../api/types";

const MAX_WS_RETRIES = 8;
const BASE_RECONNECT_MS = 1_000;
const MAX_RECONNECT_MS = 30_000;
const MAX_MESSAGES = 200;

export const TERMINAL_RUN_STATUSES = new Set([
  "succeeded",
  "failed",
  "partial",
  "partial_failed",
  "cancelled"
]);

export const isTerminalRunStatus = (status?: string | null): boolean =>
  Boolean(status && TERMINAL_RUN_STATUSES.has(status));

export type RunLiveEventType = "log_line" | "scan_progress" | "job_status" | "run_snapshot" | "error";

export interface RunLiveEvent {
  type: RunLiveEventType | string;
  ts?: string;
  level?: string;
  message?: string;
  job_id?: string;
  table_id?: string;
  table_ref?: string;
  chunk_id?: number;
  operation?: string;
  engine?: string;
  status?: string;
  event?: string;
  run?: RunRead;
  jobs?: JobRead[];
  detail?: string;
  [key: string]: unknown;
}

export interface RunLiveSnapshot {
  run?: RunRead;
  jobs?: JobRead[];
}

// "scan_progress" is deliberately excluded: the scanner worker publishes it alongside a
// "log_line" for the same table event, carrying the same message -- keeping both here doubled
// every line rendered in the live scan log. "log_line" alone is sufficient for display; nothing
// in the frontend reads the "scan_progress"-specific fields (event sub-type) separately.
const isLiveLogEvent = (event: RunLiveEvent): boolean =>
  event.type === "log_line" || event.type === "job_status";

const eventKey = (event: RunLiveEvent): string =>
  JSON.stringify({
    type: event.type,
    ts: event.ts,
    message: event.message,
    job_id: event.job_id,
    status: event.status,
    event: event.event
  });

export const useRunLiveEvents = (runId: UUID, token: string) => {
  const [messages, setMessages] = useState<RunLiveEvent[]>([]);
  const [snapshot, setSnapshot] = useState<RunLiveSnapshot | null>(null);
  const [wsOpen, setWsOpen] = useState(false);
  const [wsExhausted, setWsExhausted] = useState(false);
  const [reconnectAttempt, setReconnectAttempt] = useState(0);
  const [runTerminal, setRunTerminal] = useState(false);
  const [manualReconnectKey, setManualReconnectKey] = useState(0);
  const socketRef = useRef<WebSocket | null>(null);
  const retryTimerRef = useRef<number | null>(null);
  const retryCountRef = useRef(0);
  const runTerminalRef = useRef(false);
  const seenKeysRef = useRef<Set<string>>(new Set());

  const markRunTerminal = useCallback((status?: string | null) => {
    if (!isTerminalRunStatus(status)) {
      return;
    }
    runTerminalRef.current = true;
    setRunTerminal(true);
  }, []);

  const clearRetryTimer = useCallback(() => {
    if (retryTimerRef.current !== null) {
      window.clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
  }, []);

  const scheduleReconnect = useCallback(() => {
    if (retryCountRef.current >= MAX_WS_RETRIES) {
      setWsExhausted(true);
      setWsOpen(false);
      return;
    }
    const delay = Math.min(BASE_RECONNECT_MS * 2 ** retryCountRef.current, MAX_RECONNECT_MS);
    retryCountRef.current += 1;
    setReconnectAttempt(retryCountRef.current);
    retryTimerRef.current = window.setTimeout(() => {
      setManualReconnectKey((current) => current + 1);
    }, delay);
  }, []);

  const handleManualReconnect = useCallback(() => {
    clearRetryTimer();
    retryCountRef.current = 0;
    setReconnectAttempt(0);
    setWsExhausted(false);
    setManualReconnectKey((current) => current + 1);
  }, [clearRetryTimer]);

  const ingestEvent = useCallback((event: RunLiveEvent) => {
    if (event.type === "run_snapshot") {
      setSnapshot({ run: event.run, jobs: event.jobs });
      markRunTerminal(event.run?.status);
      return;
    }
    if (event.type === "error") {
      ingestEvent({
        type: "log_line",
        message: event.detail ?? event.message ?? "Live log error",
        level: "error",
        ts: event.ts ?? new Date().toISOString()
      });
      return;
    }
    if (!isLiveLogEvent(event)) {
      return;
    }
    const key = eventKey(event);
    if (seenKeysRef.current.has(key)) {
      return;
    }
    seenKeysRef.current.add(key);
    setMessages((current) => [event, ...current].slice(0, MAX_MESSAGES));
  }, [markRunTerminal]);

  const seenRunId = useRef(runId);
  useEffect(() => {
    clearRetryTimer();
    const runChanged = seenRunId.current !== runId;
    seenRunId.current = runId;
    if (runChanged) {
      retryCountRef.current = 0;
      setReconnectAttempt(0);
      setRunTerminal(false);
      runTerminalRef.current = false;
      setWsExhausted(false);
      setMessages([]);
      setSnapshot(null);
      seenKeysRef.current = new Set();
    }
    setWsOpen(false);

    const socket = new WebSocket(wsRunUrl(runId, token));
    socketRef.current = socket;

    socket.onopen = () => {
      retryCountRef.current = 0;
      setReconnectAttempt(0);
      setWsExhausted(false);
      setWsOpen(true);
    };
    socket.onmessage = (messageEvent) => {
      try {
        const payload = JSON.parse(messageEvent.data) as RunLiveEvent;
        ingestEvent(payload);
      } catch {
        ingestEvent({ type: "log_line", message: messageEvent.data, ts: new Date().toISOString() });
      }
    };
    socket.onerror = () => setWsOpen(false);
    socket.onclose = () => {
      setWsOpen(false);
      if (socketRef.current === socket && !runTerminalRef.current && messageEvent.code !== 1008) {
        scheduleReconnect();
      }
    };

    return () => {
      socketRef.current = null;
      clearRetryTimer();
      socket.close();
    };
  }, [runId, token, manualReconnectKey, clearRetryTimer, scheduleReconnect, ingestEvent]);

  // messages already only ever contains "log_line"/"job_status" events -- ingestEvent filters
  // via isLiveLogEvent before anything is added to state -- so no further filtering is needed
  // here.
  const logMessages = messages;

  return {
    messages: logMessages,
    allEvents: messages,
    snapshot,
    wsOpen,
    wsExhausted,
    runTerminal,
    reconnectAttempt,
    manualReconnect: handleManualReconnect
  };
};
