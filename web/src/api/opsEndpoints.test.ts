import { afterEach, describe, expect, it, vi } from "vitest";

import { getDeepHealth, getMetricsText } from "./client";

const stubFetch = (body: string, init: ResponseInit) => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(body, init));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
};

describe("ops endpoints", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads deep health from the admin API with the bearer token, even on 503", async () => {
    const payload = { status: "degraded", checks: { redis: { status: "error" } } };
    const fetchMock = stubFetch(JSON.stringify(payload), { status: 503 });

    await expect(getDeepHealth({ token: "tok" })).resolves.toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/ops/healthz/deep", {
      headers: { Authorization: "Bearer tok" }
    });
  });

  it("reads metrics from the admin API with the bearer token", async () => {
    const fetchMock = stubFetch("# HELP a\na 1\nb 2", { status: 200 });

    await expect(getMetricsText({ token: "tok" }, 2)).resolves.toBe("# HELP a\na 1\n… (1 more lines)");
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/ops/metrics", {
      headers: { Authorization: "Bearer tok" }
    });
  });
});
