import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getOverview = vi.fn();
const api = {};

vi.mock("../api/client", () => ({
  getSmartMaintenanceOverview: (...args: unknown[]) => getOverview(...args)
}));
vi.mock("../lib/auth", () => ({
  useAuth: () => ({ api })
}));

import { useCommitEventStream } from "./useCommitEventStream";

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe("useCommitEventStream", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    getOverview.mockReset();
    getOverview.mockResolvedValue({});
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("makes one overview request per pollMs even when onTick changes identity every render", async () => {
    let ticks = 0;
    const { rerender } = renderHook(() =>
      useCommitEventStream({
        pollMs: 15_000,
        // A fresh inline callback on every render, as SmartMaintenance passes it.
        onTick: () => {
          ticks += 1;
        }
      })
    );
    await flush();
    expect(getOverview).toHaveBeenCalledTimes(1);
    expect(ticks).toBe(1);

    for (let i = 0; i < 5; i += 1) rerender();
    await flush();
    expect(getOverview).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(getOverview).toHaveBeenCalledTimes(2);

    rerender();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(getOverview).toHaveBeenCalledTimes(3);
    expect(ticks).toBe(3);
  });

  it("calls the latest onTick", async () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = renderHook(({ onTick }) => useCommitEventStream({ pollMs: 5_000, onTick }), {
      initialProps: { onTick: first }
    });
    await flush();
    expect(first).toHaveBeenCalledTimes(1);
    rerender({ onTick: second });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("skips polling while the tab is hidden", async () => {
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    renderHook(() => useCommitEventStream({ pollMs: 5_000 }));
    await flush();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(getOverview).not.toHaveBeenCalled();

    visibility.mockReturnValue("visible");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    expect(getOverview).toHaveBeenCalledTimes(1);
    visibility.mockRestore();
  });

  it("does nothing when disabled", async () => {
    renderHook(() => useCommitEventStream({ enabled: false }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(getOverview).not.toHaveBeenCalled();
  });
});
