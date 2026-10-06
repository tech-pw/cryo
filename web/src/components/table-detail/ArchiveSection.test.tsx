import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ArchiveSection } from "./ArchiveSection";

const apiFetch = vi.fn();

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({ api: { token: "t" }, token: "t", role: "viewer" })
}));

vi.mock("../../api/client", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args)
}));

const summary = {
  policy_name: "cdp-default",
  read_evidence: "missing",
  read_evidence_copy: "No evidence — Auto only",
  target_label: "Archive-instant",
  projected_savings_usd_month: null,
  realized_savings_usd_month: null,
  partition_count: 1,
  eligible_count: 0
};

const partitions = {
  items: [
    {
      partition_key: "event_date=2025-06-03",
      partition_value: "event_date=2025-06-03",
      age_days: 485,
      suggestion_label: "Archive-instant",
      reason_codes: ["no_read_evidence", "no_signal"],
      evidence_copy: "No evidence — Auto only",
      net_saving_usd_month: null
    }
  ],
  total: 1,
  limit: 50,
  offset: 0
};

const plan = {
  dry_run: true,
  applied: false,
  writes: false,
  target_label: "Auto",
  object_count: null,
  byte_count: null,
  request_cost_usd: null,
  guardrails: [{ name: "versioning", status: "not_checked" }],
  partitions_selected: 0
};

const renderSection = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ArchiveSection tableId="table-1" />
    </QueryClientProvider>
  );
};

describe("ArchiveSection", () => {
  it("shows reason codes and never suggests Glacier IR without evidence", async () => {
    apiFetch.mockImplementation((path: string) =>
      String(path).includes("/partitions") ? Promise.resolve(partitions) : Promise.resolve(summary)
    );
    renderSection();

    expect(await screen.findByRole("heading", { name: "Archive" })).toBeInTheDocument();
    expect(await screen.findByText("no_read_evidence")).toBeInTheDocument();
    expect(screen.getByText("no_signal")).toBeInTheDocument();
    expect(screen.getAllByText("No evidence — Auto only").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Not estimated").length).toBeGreaterThan(0);
    expect(screen.queryByText("Archive-instant")).not.toBeInTheDocument();
    expect(screen.queryByText(/GLACIER/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /apply|restore/i })).not.toBeInTheDocument();
  });

  it("previews a dry run and does not estimate unmeasured request cost", async () => {
    apiFetch.mockImplementation((path: string, _api: unknown, options?: { method?: string }) => {
      if (options?.method === "POST") return Promise.resolve(plan);
      if (String(path).includes("/partitions")) return Promise.resolve(partitions);
      return Promise.resolve(summary);
    });
    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: "Preview plan" }));

    expect(await screen.findByText("Dry run")).toBeInTheDocument();
    expect(screen.getByText(/request cost/)).toHaveTextContent("Not estimated");
    expect(screen.queryByRole("button", { name: /apply|restore/i })).not.toBeInTheDocument();
  });
});
