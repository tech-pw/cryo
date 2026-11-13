import { afterEach, describe, expect, it, vi } from "vitest";

import { apiFetch, clearApiEtagCache, repairSilentDataLoss } from "./client";

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
  clearApiEtagCache();
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

  it("sends If-None-Match, reuses payload on 304, coalesces concurrent GETs, and isolates tokens (#533)", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ total: 12 }), {
          status: 200,
          headers: { "Content-Type": "application/json", ETag: '"etag-v1"' }
        })
      )
      .mockResolvedValueOnce(new Response(null, { status: 304, headers: { ETag: '"etag-v1"' } }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ total: 99 }), {
          status: 200,
          headers: { "Content-Type": "application/json", ETag: '"etag-other"' }
        })
      );

    // Concurrent GETs with the same token coalesce into one network request.
    const [first, coalesced] = await Promise.all([
      apiFetch<{ total: number }>("/dashboards/cost", { token: "tok-a" }),
      apiFetch<{ total: number }>("/dashboards/cost", { token: "tok-a" })
    ]);
    expect(first).toEqual({ total: 12 });
    expect(coalesced).toEqual({ total: 12 });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // Subsequent GET sends If-None-Match and reuses cached payload on 304.
    const revalidated = await apiFetch<{ total: number }>("/dashboards/cost", { token: "tok-a" });
    expect(revalidated).toEqual({ total: 12 });
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/v1/dashboards/cost",
      expect.objectContaining({
        headers: expect.objectContaining({ "If-None-Match": '"etag-v1"' })
      })
    );

    // Different token never reuses tok-a's ETag or payload.
    const otherRole = await apiFetch<{ total: number }>("/dashboards/cost", { token: "tok-b" });
    expect(otherRole).toEqual({ total: 99 });
    const thirdHeaders = fetchMock.mock.calls[2]?.[1]?.headers as Record<string, string>;
    expect(thirdHeaders["If-None-Match"]).toBeUndefined();
  });
});
