import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Skillsets } from "./Skillsets";
import type { SkillsetRead } from "../api/types";

let currentRole: "admin" | "viewer" = "admin";

vi.mock("../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: currentRole,
    subject: "tester",
    loading: false,
    error: null,
    isAuthenticated: true,
    signIn: vi.fn(),
    signOut: vi.fn()
  })
}));

const listSkillsets = vi.fn();
const listLlmObservations = vi.fn();
const activateSkillset = vi.fn();
const rollbackSkillset = vi.fn();
const promoteSkillset = vi.fn();
const createSkillset = vi.fn();

vi.mock("../api/client", () => ({
  listSkillsets: (...args: unknown[]) => listSkillsets(...args),
  listLlmObservations: (...args: unknown[]) => listLlmObservations(...args),
  activateSkillset: (...args: unknown[]) => activateSkillset(...args),
  rollbackSkillset: (...args: unknown[]) => rollbackSkillset(...args),
  promoteSkillset: (...args: unknown[]) => promoteSkillset(...args),
  createSkillset: (...args: unknown[]) => createSkillset(...args)
}));

const ROWS: SkillsetRead[] = [
  {
    id: "sk-1",
    name: "iceberg_advisor",
    version: "1.0.0",
    prompts: { table_insight: "Stats:\n{stats}\n\nBe concise." },
    tools: [],
    created_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metrics: { acceptance_rate: 0.8, confidence: 0.7, avg_score: 0.9, outcomes_count: 12 }
  },
  {
    id: "sk-2",
    name: "iceberg_advisor",
    version: "1.1.0",
    prompts: { table_insight: "Stats:\n{stats}\n\nBe brief." },
    tools: [],
    created_at: "2026-02-01T00:00:00Z",
    is_active: false,
    metrics: {}
  }
];

const renderPage = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/skillsets"]}>
        <Skillsets />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

const inactiveRow = async () => {
  const version = await screen.findByText("1.1.0");
  return version.closest("tr") as HTMLElement;
};

beforeEach(() => {
  vi.clearAllMocks();
  currentRole = "admin";
  listSkillsets.mockResolvedValue({ skillsets: ROWS, generated_at: "2026-01-01T00:00:00Z" });
  listLlmObservations.mockResolvedValue({ items: [], generated_at: "2026-01-01T00:00:00Z" });
});

describe("Skillsets lifecycle confirmations", () => {
  it("requires confirmation before activating a version, showing the real current/target versions", async () => {
    renderPage();
    const row = await inactiveRow();
    fireEvent.click(within(row).getByRole("button", { name: "Activate" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Activate iceberg_advisor@1.1.0?")).toBeInTheDocument();
    expect(within(dialog).getByText("1.0.0")).toBeInTheDocument();
    expect(within(dialog).getByText("1.1.0")).toBeInTheDocument();
    expect(activateSkillset).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Activate" }));
    await waitFor(() =>
      expect(activateSkillset).toHaveBeenCalledWith(expect.anything(), "iceberg_advisor", { version: "1.1.0" })
    );
  });

  it("requires confirmation before rolling back, describing it as reactivating an older version", async () => {
    renderPage();
    const row = await inactiveRow();
    fireEvent.click(within(row).getByRole("button", { name: "Rollback" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Roll back iceberg_advisor to 1.1.0?")).toBeInTheDocument();
    expect(within(dialog).getByText(/does not delete any version/i)).toBeInTheDocument();
    expect(rollbackSkillset).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Roll back" }));
    await waitFor(() =>
      expect(rollbackSkillset).toHaveBeenCalledWith(expect.anything(), "iceberg_advisor", { version: "1.1.0" })
    );
  });

  it("disables Activate/Rollback/Promote/Create for non-admin roles", async () => {
    currentRole = "viewer";
    renderPage();
    const row = await inactiveRow();
    expect(within(row).getByRole("button", { name: "Activate" })).toBeDisabled();
    expect(within(row).getByRole("button", { name: "Rollback" })).toBeDisabled();
    expect(within(row).getByRole("button", { name: "Promote" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Create candidate" })).toBeDisabled();
  });
});

describe("Skillsets create-candidate form", () => {
  it("shows structured prompt fields by default and reveals raw JSON only on request", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Create candidate" }));

    expect(await screen.findByLabelText("Table insight")).toBeInTheDocument();
    expect(screen.getByLabelText("Plan explanation")).toBeInTheDocument();
    expect(screen.getByLabelText("Savings forecast")).toBeInTheDocument();
    expect(screen.queryByLabelText("Prompts (advanced JSON)")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Advanced: edit raw JSON" }));
    expect(await screen.findByLabelText("Prompts (advanced JSON)")).toBeInTheDocument();
  });

  it("shows a focused error summary linking to invalid fields on submit", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Create candidate" }));

    const versionInput = await screen.findByPlaceholderText("1.2.0");
    fireEvent.change(versionInput, { target: { value: "not-a-version" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findByText(/fix the following before continuing/i)).toBeInTheDocument();
    expect(createSkillset).not.toHaveBeenCalled();
  });

  it("requires a separate confirmation with current/target version before activate-on-create", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Create candidate" }));

    const versionInput = await screen.findByPlaceholderText("1.2.0");
    fireEvent.change(versionInput, { target: { value: "2.0.0" } });
    fireEvent.click(screen.getByLabelText("Activate immediately after create"));

    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Create and activate immediately?")).toBeInTheDocument();
    expect(within(dialog).getByText(/iceberg_advisor@2.0.0/)).toBeInTheDocument();
    expect(within(dialog).getByText("1.0.0")).toBeInTheDocument();
    expect(createSkillset).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Create & activate" }));
    await waitFor(() => expect(createSkillset).toHaveBeenCalled());
    expect(createSkillset.mock.calls[0][1]).toMatchObject({ name: "iceberg_advisor", version: "2.0.0", activate: true });
  });

  it("does not require confirmation when activate-on-create is left unchecked", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Create candidate" }));

    const versionInput = await screen.findByPlaceholderText("1.2.0");
    fireEvent.change(versionInput, { target: { value: "2.0.0" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(createSkillset).toHaveBeenCalled());
    expect(createSkillset.mock.calls[0][1]).toMatchObject({ activate: false });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });
});

describe("Skillsets prompt preview harness", () => {
  it("renders the substituted template only once every referenced variable is filled in", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Create candidate" }));

    const tableInsight = await screen.findByLabelText("Table insight");
    fireEvent.change(tableInsight, { target: { value: "Stats:\n{stats}\n\nBe concise." } });

    expect(await screen.findByText("Unrendered (fill every variable above to preview)")).toBeInTheDocument();

    const statsInput = screen.getByPlaceholderText("Sample value for stats");
    fireEvent.change(statsInput, { target: { value: "42 files, 3 partitions" } });

    expect(await screen.findByText("Rendered preview")).toBeInTheDocument();
    expect(screen.getByText(/42 files, 3 partitions/)).toBeInTheDocument();
  });
});
