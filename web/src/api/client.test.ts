import { afterEach, describe, expect, it, vi } from "vitest";

import { apiFetch, repairSilentDataLoss } from "./client";

describe("repairSilentDataLoss", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("explicitly requests a non-committing dry run", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "accepted", message: "queued" }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    await repairSilentDataLoss({ token: "test-token" }, "table-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/advanced/silent-data-loss/table-1/repair",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ commit: false })
      })
    );
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("apiFetch correlation", () => {
  it("sends a correlation ID and presents the returned ID on failures", async () => {
    vi.spyOn(crypto, "randomUUID").mockReturnValue("01915f3e-7a2d-7000-8000-000000000001");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "destination unavailable" }), {
        status: 503,
        headers: {
          "Content-Type": "application/json",
          "X-Correlation-ID": "server-correlation-42"
        }
      })
    );

    await expect(apiFetch("/destinations", { token: "test-token" })).rejects.toMatchObject({
      name: "ApiError",
      status: 503,
      correlationId: "server-correlation-42",
      message: "destination unavailable · Correlation server-correlation-42"
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/destinations",
      expect.objectContaining({
        headers: expect.objectContaining({
          "X-Correlation-ID": "01915f3e-7a2d-7000-8000-000000000001"
        })
      })
    );
  });
});
