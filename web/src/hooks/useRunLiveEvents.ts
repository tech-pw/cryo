import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { wsRunUrl } from "../api/client";
import type { JobRead, RunRead, UUID } from "../api/types";

const MAX_WS_RETRIES = 8;
const BASE_RECONNECT_MS = 1_000;
const MAX_RECONNECT_MS = 30_000;
const MAX_MESSAGES = 200;

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

const isLiveLogEvent = (event: RunLiveEvent): boolean =>
  event.type === "log_line" || event.type === "scan_progress" || event.type === "job_status";

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
  const [manualReconnectKey, setManualReconnectKey] = useState(0);
  const socketRef = useRef<WebSocket | null>(null);
  const retryTimerRef = useRef<number | null>(null);
  const retryCountRef = useRef(0);
  const seenKeysRef = useRef<Set<string>>(new Set());

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
  }, []);

  useEffect(() => {
    clearRetryTimer();
    retryCountRef.current = 0;
    setReconnectAttempt(0);
    setWsExhausted(false);
    setWsOpen(false);
    setMessages([]);
    setSnapshot(null);
    seenKeysRef.current = new Set();

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
      if (socketRef.current === socket) {
        scheduleReconnect();
      }
    };

    return () => {
      socketRef.current = null;
      clearRetryTimer();
      socket.close();
    };
  }, [runId, token, manualReconnectKey, clearRetryTimer, scheduleReconnect, ingestEvent]);

  const logMessages = useMemo(
    () => messages.filter((item) => item.type === "log_line" || item.type === "scan_progress"),
    [messages]
  );

  return {
    messages: logMessages,
    allEvents: messages,
    snapshot,
    wsOpen,
    wsExhausted,
    reconnectAttempt,
    manualReconnect: handleManualReconnect
  };
};
