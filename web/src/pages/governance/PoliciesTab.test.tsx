import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { PoliciesTab } from "./PoliciesTab";

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({ api: { token: "t" }, token: "t", role: "operator" })
}));

vi.mock("../../api/client", () => ({
  listTierPolicies: vi.fn().mockResolvedValue([]),
  createTierPolicy: vi.fn(),
  patchTierPolicy: vi.fn(),
  deleteTierPolicy: vi.fn(),
  getS3TieringRecommendations: vi.fn().mockResolvedValue({ recommendations: [], total_potential_savings_usd: 0 }),
  getTierCandidates: vi.fn(),
  getGitOpsStatus: vi.fn().mockResolvedValue({ configured: false, policies: [], conflicts: [] }),
  listAbacPolicies: vi.fn().mockResolvedValue({ items: [], enforcement_note: "note" }),
  listSources: vi.fn().mockResolvedValue([{ id: "source-1", name: "demo" }])
}));

describe("PoliciesTab storage classes", () => {
  it("offers Cool, Archive-instant, and Auto, not offline classes", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <PoliciesTab />
        </MemoryRouter>
      </QueryClientProvider>
    );

    fireEvent.click(screen.getByRole("button", { name: "Add policy" }));
    const select = await screen.findByLabelText("Storage class");
    const options = [...(select as HTMLSelectElement).options];
    expect(options.map((option) => option.textContent)).toEqual(["Cool", "Archive-instant", "Auto"]);
    expect(options.map((option) => option.value)).toEqual([
      "STANDARD_IA",
      "GLACIER_IR",
      "INTELLIGENT_TIERING"
    ]);
    expect(screen.queryByRole("option", { name: "GLACIER" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "DEEP_ARCHIVE" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /offline/i })).not.toBeInTheDocument();
  });
});
