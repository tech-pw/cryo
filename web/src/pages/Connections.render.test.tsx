import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { Connections } from "./Connections";

vi.mock("../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: "operator",
    subject: "tester",
    loading: false,
    error: null,
    isAuthenticated: true,
    signIn: vi.fn(),
    signOut: vi.fn()
  })
}));

vi.mock("../api/client", () => ({
  listSources: vi.fn().mockResolvedValue([]),
  listDestinations: vi.fn().mockResolvedValue([]),
  listAIProviders: vi.fn().mockResolvedValue([]),
  listAIProviderTypes: vi.fn().mockResolvedValue([]),
  listSlackConfigs: vi.fn().mockResolvedValue([]),
  getAwsIntegration: vi.fn().mockResolvedValue(null),
  getCatalogLatency: vi.fn().mockResolvedValue(null),
  createSource: vi.fn(),
  patchSource: vi.fn(),
  purgeSource: vi.fn(),
  createDestination: vi.fn(),
  patchDestination: vi.fn(),
  purgeDestination: vi.fn(),
  testDestination: vi.fn(),
  testDestinationConfig: vi.fn(),
  createAIProvider: vi.fn(),
  patchAIProvider: vi.fn(),
  deleteAIProvider: vi.fn(),
  testAIProvider: vi.fn(),
  createSlackConfig: vi.fn(),
  patchSlackConfig: vi.fn(),
  deleteSlackConfig: vi.fn(),
  testSlackConfig: vi.fn(),
  testSlackConfigValues: vi.fn(),
  triggerDailyReport: vi.fn(),
  getSourceNamespaces: vi.fn(),
  testSource: vi.fn(),
  listRuns: vi.fn().mockResolvedValue([]),
  triggerScan: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  }
}));

const renderConnections = (initialEntries: string[]) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <Connections />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("Connections tab URL sync", () => {
  it("defaults to the Lakehouses tab with no URL param", async () => {
    renderConnections(["/connections"]);
    expect(await screen.findByRole("tab", { name: "Lakehouses", selected: true })).toBeInTheDocument();
  });

  it("honors ?tab=destinations and shows the Engines tab content", async () => {
    renderConnections(["/connections?tab=destinations"]);
    expect(await screen.findByRole("tab", { name: "Engines", selected: true })).toBeInTheDocument();
  });

  it("falls back to the default tab for a non-admin trying ?tab=ops", async () => {
    renderConnections(["/connections?tab=ops"]);
    expect(await screen.findByRole("tab", { name: "Lakehouses", selected: true })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Ops" })).not.toBeInTheDocument();
  });

  it("shows the Slack delivery and connections sections on the Slack tab", async () => {
    renderConnections(["/connections?tab=slack"]);
    expect(await screen.findByRole("heading", { name: "Slack delivery" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Manual daily report" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Slack connections" })).toBeInTheDocument();
  });
});

describe("Connections URL-backed edit context", () => {
  it("opens the new-lakehouse drawer directly from ?edit=source&editId=new", async () => {
    renderConnections(["/connections?edit=source&editId=new"]);
    expect(await screen.findByRole("heading", { name: /add lakehouse|new lakehouse|source/i })).toBeInTheDocument();
  });

  it("shows the progressive Identity/Review sections and no error summary before any submit attempt", async () => {
    renderConnections(["/connections?edit=source&editId=new"]);
    await screen.findByRole("heading", { name: /add lakehouse|new lakehouse|source/i });
    expect(screen.getByText("Identity")).toBeInTheDocument();
    expect(screen.getByText("Review")).toBeInTheDocument();
    expect(screen.queryByRole("alert", { name: /fix the following/i })).not.toBeInTheDocument();
  });

  it("opens the new-engine drawer directly from ?edit=destination&editId=new", async () => {
    renderConnections(["/connections?edit=destination&editId=new"]);
    expect(await screen.findByRole("heading", { name: /new engine/i })).toBeInTheDocument();
    expect(screen.getByText("Identity")).toBeInTheDocument();
    expect(screen.getByText("Review")).toBeInTheDocument();
  });
});
