import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetApiVersionCache } from "../../lib/buildInfo";
import { VersionFooter } from "./VersionFooter";

const renderFooter = () =>
  render(
    <MemoryRouter>
      <VersionFooter />
    </MemoryRouter>
  );

describe("VersionFooter", () => {
  beforeEach(() => resetApiVersionCache());
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("shows the API version and short commit, linking to Settings > About", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ version: "v0.1.6", git_sha: "abcdef1234567890", built_at: null, schema_revision: "r1" }),
        { status: 200 }
      )
    );
    vi.stubGlobal("fetch", fetchMock);
    renderFooter();
    const link = await screen.findByRole("link", { name: /Cryo v0\.1\.6 · abcdef1/ });
    expect(link.getAttribute("href")).toBe("/settings/about");
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/version");
    expect(screen.queryByLabelText("Web and API versions differ")).toBeNull();
  });

  it("warns when the web build differs from the API", async () => {
    vi.stubEnv("VITE_CRYO_VERSION", "v0.1.5");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ version: "v0.1.6", git_sha: null, built_at: null, schema_revision: null }), {
          status: 200
        })
      )
    );
    renderFooter();
    expect(await screen.findByLabelText("Web and API versions differ")).toBeTruthy();
  });

  it("renders nothing when the API can't be reached", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const { container } = renderFooter();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(container.textContent).toBe("");
  });
});
