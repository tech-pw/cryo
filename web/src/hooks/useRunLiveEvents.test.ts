import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createWsTicket } from "../api/client";
import { isTerminalRunStatus, useRunLiveEvents } from "./useRunLiveEvents";

vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  createWsTicket: vi.fn()
}));

let ticketSeq = 0;

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(public url: string) {
    MockWebSocket.instances.push(this);
    queueMicrotask(() => this.onopen?.());
  }

  close() {
    this.onclose?.();
  }

  emit(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent<string>);
  }
}

describe("useRunLiveEvents", () => {
  beforeEach(() => {
    ticketSeq = 0;
    vi.mocked(createWsTicket).mockReset();
    vi.mocked(createWsTicket).mockImplementation(async () => {
      ticketSeq += 1;
      return { ticket: `ticket-${ticketSeq}`, expires_in_sec: 30 };
    });
  });

  afterEach(() => {
    MockWebSocket.instances = [];
    vi.unstubAllGlobals();
  });

  it("treats a partial batch run as finished", () => {
    expect(isTerminalRunStatus("partial")).toBe(true);
    expect(isTerminalRunStatus("running")).toBe(false);
  });

  it("captures log lines and run snapshots from websocket messages", async () => {
    vi.stubGlobal("WebSocket", MockWebSocket as unknown as typeof WebSocket);

    const { result } = renderHook(() => useRunLiveEvents("run-1", "token"));

    await waitFor(() => {
      expect(result.current.wsOpen).toBe(true);
    });

    act(() => {
      MockWebSocket.instances[0]?.emit({
        type: "log_line",
        ts: "2026-07-06T00:00:00Z",
        message: "Started maintenance job"
      });
      MockWebSocket.instances[0]?.emit({
        type: "run_snapshot",
        run: { id: "run-1", kind: "adhoc", status: "running", created_at: "2026-07-06T00:00:00Z" },
        jobs: []
      });
    });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]?.message).toBe("Started maintenance job");
    expect(result.current.snapshot?.run?.id).toBe("run-1");
  });

  it("does not reconnect when the server closes after a terminal run snapshot", async () => {
    vi.stubGlobal("WebSocket", MockWebSocket as unknown as typeof WebSocket);

    const { result } = renderHook(() => useRunLiveEvents("run-1", "token"));

    await waitFor(() => {
      expect(result.current.wsOpen).toBe(true);
    });

    act(() => {
      MockWebSocket.instances[0]?.emit({
        type: "run_snapshot",
        run: { id: "run-1", kind: "adhoc", status: "succeeded", created_at: "2026-07-06T00:00:00Z" },
        jobs: []
      });
      MockWebSocket.instances[0]?.close();
    });

    await waitFor(() => {
      expect(result.current.wsOpen).toBe(false);
    });

    expect(result.current.runTerminal).toBe(true);
    expect(result.current.reconnectAttempt).toBe(0);
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("does not render both scan_progress and log_line for the same table event", async () => {
    // The scanner worker publishes a "scan_progress" event alongside every "log_line" for the
    // same table event, carrying the same message -- rendering both doubled every line in the
    // live scan log. Regression test for that.
    vi.stubGlobal("WebSocket", MockWebSocket as unknown as typeof WebSocket);

    const { result } = renderHook(() => useRunLiveEvents("run-1", "token"));

    await waitFor(() => {
      expect(result.current.wsOpen).toBe(true);
    });

    act(() => {
      MockWebSocket.instances[0]?.emit({
        type: "scan_progress",
        ts: "2026-07-06T00:00:00Z",
        event: "table_result",
        message: "Scanned demo.events.silver_bookmarks"
      });
      MockWebSocket.instances[0]?.emit({
        type: "log_line",
        ts: "2026-07-06T00:00:00Z",
        message: "Scanned demo.events.silver_bookmarks"
      });
    });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]?.type).toBe("log_line");
  });

  it("opens the socket with a single-use ticket and never puts the token in the URL", async () => {
    vi.stubGlobal("WebSocket", MockWebSocket as unknown as typeof WebSocket);

    const { result } = renderHook(() => useRunLiveEvents("run-1", "secret-bearer"));

    await waitFor(() => {
      expect(result.current.wsOpen).toBe(true);
    });

    expect(createWsTicket).toHaveBeenCalledWith("run-1", { token: "secret-bearer" });
    const url = new URL(MockWebSocket.instances[0]!.url);
    expect(url.pathname).toBe("/api/v1/ws/runs/run-1");
    expect(url.searchParams.get("ticket")).toBe("ticket-1");
    expect(url.searchParams.has("token")).toBe(false);
    expect(MockWebSocket.instances[0]!.url).not.toContain("secret-bearer");
  });

  it("fetches a fresh ticket for every reconnect", async () => {
    vi.stubGlobal("WebSocket", MockWebSocket as unknown as typeof WebSocket);

    const { result } = renderHook(() => useRunLiveEvents("run-1", "token"));
    await waitFor(() => {
      expect(result.current.wsOpen).toBe(true);
    });

    act(() => {
      result.current.manualReconnect();
    });

    await waitFor(() => {
      expect(MockWebSocket.instances).toHaveLength(2);
    });
    expect(createWsTicket).toHaveBeenCalledTimes(2);
    expect(new URL(MockWebSocket.instances[1]!.url).searchParams.get("ticket")).toBe("ticket-2");
  });
});
