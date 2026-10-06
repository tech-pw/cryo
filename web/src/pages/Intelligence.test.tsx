import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Intelligence } from "./Intelligence";

vi.mock("../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: "admin",
    subject: "tester",
    loading: false,
    error: null,
    isAuthenticated: true,
    signIn: vi.fn(),
    signOut: vi.fn()
  })
}));

const getBanditStatus = vi.fn();
const getCostModelEvaluate = vi.fn();
const getQPPConfidence = vi.fn();
const resetBandit = vi.fn();

vi.mock("../api/client", () => ({
  getBanditStatus: (...args: unknown[]) => getBanditStatus(...args),
  getCostModelEvaluate: (...args: unknown[]) => getCostModelEvaluate(...args),
  getQPPConfidence: (...args: unknown[]) => getQPPConfidence(...args),
  resetBandit: (...args: unknown[]) => resetBandit(...args)
}));

const renderIntelligence = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Intelligence />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("Intelligence page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getBanditStatus.mockResolvedValue({
      observations: 42,
      confident: true,
      config: { high_load_ratio: 0.8, high_load_fraction: 0.5, per_replica_cap: 4 }
    });
    getCostModelEvaluate.mockResolvedValue({
      mae_learned: 1.2,
      mae_heuristic: 2.4,
      beats_heuristic: true,
      band_coverage: 0.9,
      n_samples: 50
    });
    getQPPConfidence.mockResolvedValue({
      observations: 10,
      confidence: 0.7,
      min_observations: 5,
      using_trained_model: true,
      qpp_enabled: true
    });
  });

  it("points operators to the Ask Cryo drawer and does not render chat forms", async () => {
    renderIntelligence();
    expect((await screen.findAllByText(/Ask Cryo lives in the drawer/)).length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("Question")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Prompt")).not.toBeInTheDocument();
    expect(screen.queryByText("Chat & tool calls (advanced)")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Tool")).not.toBeInTheDocument();
  });

  it("keeps experimental governor diagnostics", async () => {
    renderIntelligence();
    expect(await screen.findByText("Experimental diagnostics")).toBeInTheDocument();
    expect(await screen.findByText("Governor bandit")).toBeInTheDocument();
  });
});
