import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { getRuntimeFlags } from "./runtimeFlags";

vi.mock("../api/client", () => ({
  apiFetch: vi.fn()
}));

describe("getRuntimeFlags", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("reads GET /runtime", async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      native_engine_enabled: false,
      maintenance_writes_enabled: true,
      native_engine_fallback: "the configured Spark SQL fallback"
    });

    await expect(getRuntimeFlags({ token: "test-token" })).resolves.toEqual({
      native_engine_enabled: false,
      maintenance_writes_enabled: true,
      native_engine_fallback: "the configured Spark SQL fallback"
    });
    expect(apiFetch).toHaveBeenCalledWith("/runtime", { token: "test-token" });
  });
});
