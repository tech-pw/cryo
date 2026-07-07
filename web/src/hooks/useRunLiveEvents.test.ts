import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useRunLiveEvents } from "./useRunLiveEvents";

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
  afterEach(() => {
    MockWebSocket.instances = [];
    vi.unstubAllGlobals();
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
});
