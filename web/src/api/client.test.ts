import { afterEach, describe, expect, it, vi } from "vitest";

import { repairSilentDataLoss } from "./client";

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
