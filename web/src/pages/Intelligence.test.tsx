import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

const getCopilotQuestions = vi.fn();
const getBanditStatus = vi.fn();
const getCostModelEvaluate = vi.fn();
const getQPPConfidence = vi.fn();
const listMcpTools = vi.fn();
const askCopilotQuestion = vi.fn();
const searchCopilot = vi.fn();
const askCopilotNatural = vi.fn();
const aiChat = vi.fn();
const callMcpTool = vi.fn();
const resetBandit = vi.fn();

vi.mock("../api/client", () => ({
  getCopilotQuestions: (...args: unknown[]) => getCopilotQuestions(...args),
  getBanditStatus: (...args: unknown[]) => getBanditStatus(...args),
  getCostModelEvaluate: (...args: unknown[]) => getCostModelEvaluate(...args),
  getQPPConfidence: (...args: unknown[]) => getQPPConfidence(...args),
  listMcpTools: (...args: unknown[]) => listMcpTools(...args),
  askCopilotQuestion: (...args: unknown[]) => askCopilotQuestion(...args),
  searchCopilot: (...args: unknown[]) => searchCopilot(...args),
  askCopilotNatural: (...args: unknown[]) => askCopilotNatural(...args),
  aiChat: (...args: unknown[]) => aiChat(...args),
  callMcpTool: (...args: unknown[]) => callMcpTool(...args),
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
    getCopilotQuestions.mockResolvedValue([{ id: "q1", prompt: "What tables are unhealthy?" }]);
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
    listMcpTools.mockResolvedValue([{ name: "scan_table", description: "Scan a table for stats" }]);
  });

  it("labels a verified-playbook answer as deterministic with its source", async () => {
    askCopilotQuestion.mockResolvedValue({ question_id: "q1", result: { grade: "B" } });
    renderIntelligence();

    fireEvent.click(await screen.findByRole("button", { name: "Run" }));

    expect(await screen.findByText("Deterministic")).toBeInTheDocument();
    expect(screen.getByText("Verified playbook")).toBeInTheDocument();
    expect(screen.getByText(/q1/)).toBeInTheDocument();
  });

  it("labels an embedding-search answer as deterministic with similarity", async () => {
    searchCopilot.mockResolvedValue({
      source: "embedding_fallback",
      similarity: 0.87,
      answer: "Closest topic: compaction backlog."
    });
    renderIntelligence();

    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "compaction backlog?" } });
    fireEvent.click(screen.getByRole("button", { name: /Search \(verified \+ embeddings\)/ }));

    expect(await screen.findByText("Deterministic")).toBeInTheDocument();
    expect(screen.getByText("Embedding similarity match")).toBeInTheDocument();
    expect(screen.getByText(/similarity 87%/)).toBeInTheDocument();
  });

  it("labels an LLM answer as generated with provider/model", async () => {
    askCopilotNatural.mockResolvedValue({ answer: "Here's the story.", provider: "openai", model: "gpt-4o" });
    renderIntelligence();

    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "tell me a story" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask LLM/ }));

    expect(await screen.findByText("Generated")).toBeInTheDocument();
    expect(screen.getByText("openai · gpt-4o")).toBeInTheDocument();
  });

  it("shows a persistent, retryable error alert (not just a toast) when search fails", async () => {
    searchCopilot.mockRejectedValue(new Error("boom"));
    renderIntelligence();

    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "fail please" } });
    fireEvent.click(screen.getByRole("button", { name: /Search \(verified \+ embeddings\)/ }));

    expect(await screen.findByText("Search failed")).toBeInTheDocument();
    expect(screen.getByText("boom")).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: /Retry/ });
    searchCopilot.mockResolvedValue({ source: "verified", answer: "recovered" });
    fireEvent.click(retry);
    expect(await screen.findByText("recovered")).toBeInTheDocument();
  });

  it("sends a structured Table ID as chat context by default, without a raw JSON field", async () => {
    aiChat.mockResolvedValue({ response: "ok", provider: "openai", model: "gpt-4o", tokens_used: 12 });
    renderIntelligence();

    fireEvent.click(screen.getByText("Chat & tool calls (advanced)"));
    fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "hello" } });
    expect(screen.queryByLabelText("Context (advanced JSON)")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Context · Table ID (optional)"), {
      target: { value: "table-123" }
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(aiChat).toHaveBeenCalled());
    expect(aiChat).toHaveBeenCalledWith(
      { token: "test-token" },
      { prompt: "hello", context: { table_id: "table-123" } }
    );
  });

  it("falls back to advanced raw JSON for chat context when toggled, and validates it client-side", async () => {
    renderIntelligence();
    fireEvent.click(screen.getByText("Chat & tool calls (advanced)"));
    fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "hello" } });
    fireEvent.click(screen.getByText("Advanced: edit raw JSON"));

    const jsonField = screen.getByLabelText("Context (advanced JSON)");
    fireEvent.change(jsonField, { target: { value: "{not json" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect((await screen.findAllByText("Context must be valid JSON.")).length).toBeGreaterThan(0);
    expect(aiChat).not.toHaveBeenCalled();
  });

  it("does not fabricate a schema for MCP tool arguments beyond the documented Table ID key", async () => {
    renderIntelligence();
    fireEvent.click(screen.getByText("Chat & tool calls (advanced)"));
    expect(await screen.findByLabelText("Tool")).toBeInTheDocument();
    expect(screen.getByLabelText("Arguments · Table ID (optional)")).toBeInTheDocument();
    expect(screen.queryByLabelText("Arguments (advanced JSON)")).not.toBeInTheDocument();
  });
});
