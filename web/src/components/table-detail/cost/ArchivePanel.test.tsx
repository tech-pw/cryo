import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ArchivePanel } from "./ArchivePanel";

const apiFetch = vi.fn();
let role = "operator";

vi.mock("../../../lib/auth", () => ({
  useAuth: () => ({ api: { token: "t" }, token: "t", role })
}));

vi.mock("../../../lib/runtimeFlags", () => ({
  getRuntimeFlags: () => Promise.resolve({ maintenance_writes_enabled: true })
}));

vi.mock("../../../api/client", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args)
}));

const summary = {
  table_id: "table-1",
  mode: "manual",
  policy_name: null,
  read_evidence: "missing",
  target_class: null,
  projected_savings_usd_month: 3,
  partition_count: 2,
  eligible_count: 2,
  archived_partitions: 1,
  archived_gb: 4
};

const partitions = {
  items: [
    {
      partition_key: "event_date=2024-01-01",
      partition_value: "event_date=2024-01-01",
      age_days: 650,
      size_gb: 12,
      suggestion: "auto",
      suggestion_label: "Auto",
      reason_codes: ["no_read_evidence"],
      net_saving_usd_month: null,
      current_class: null
    },
    {
      partition_key: "event_date=2023-12-31",
      partition_value: "event_date=2023-12-31",
      age_days: 651,
      size_gb: 4,
      suggestion: "auto",
      suggestion_label: "Auto",
      reason_codes: [],
      net_saving_usd_month: null,
      current_class: "STANDARD_IA",
      moved_at: "2026-10-01T00:00:00Z"
    }
  ],
  total: 2,
  limit: 50,
  offset: 0
};

const preflight = {
  action: "archive",
  target_class: "INTELLIGENT_TIERING",
  target_label: "Intelligent-Tiering",
  bucket: "lake",
  partitions_selected: 1,
  object_estimate: 40,
  gb_estimate: 12,
  request_cost_usd: 0.0004,
  storage_change_usd_month: -0.1,
  retrieval_cost_usd: 0,
  early_delete_usd: 0,
  permissions: [
    { action: "s3:GetObject", status: "ok", purpose: "Read each file's class" },
    { action: "s3:PutObject", status: "missing", purpose: "Copy files to a new class" }
  ],
  guardrails: [{ name: "versioning", status: "pass", detail: "Not versioned." }],
  missing_permissions: ["s3:PutObject"],
  iam_policy: '{"Statement": ["s3:x-amz-storage-class"]}',
  warnings: [],
  blockers: [],
  requires_confirmation: false,
  can_apply: false
};

const route = (path: string, _api?: unknown, options?: { query?: { state?: string } }) => {
  if (path.includes("/preflight")) return Promise.resolve(preflight);
  if (path.includes("/partitions")) {
    const archived = options?.query?.state === "archived";
    const items = partitions.items.filter((row) => Boolean(row.current_class) === archived);
    return Promise.resolve({ ...partitions, items, total: items.length });
  }
  if (path.includes("/jobs")) return Promise.resolve({ items: [] });
  return Promise.resolve(summary);
};

const renderPanel = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ArchivePanel tableId="table-1" />
    </QueryClientProvider>
  );
};

describe("ArchivePanel", () => {
  beforeEach(() => {
    apiFetch.mockReset();
    apiFetch.mockImplementation(route);
    role = "operator";
  });

  it("hides the projected saving without read evidence and suggests Intelligent-Tiering", async () => {
    renderPanel();
    expect(await screen.findByText("Needs read evidence")).toBeInTheDocument();
    expect(await screen.findByText("event_date=2024-01-01")).toBeInTheDocument();
    expect(screen.getByText("Intelligent-Tiering")).toBeInTheDocument();
    expect(screen.queryByText("event_date=2023-12-31")).not.toBeInTheDocument();
    expect(screen.queryByText("no_read_evidence")).not.toBeInTheDocument();
  });

  it("shows the missing permission and the IAM policy before anything moves", async () => {
    renderPanel();
    await screen.findByText("event_date=2024-01-01");
    fireEvent.click(screen.getByRole("button", { name: "Archive all suggested…" }));
    expect(await screen.findByText("The source's S3 credentials need more permissions")).toBeInTheDocument();
    expect(screen.getByText(/s3:x-amz-storage-class/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Archive to Intelligent-Tiering/ })).toBeDisabled();
    const preflightCall = apiFetch.mock.calls.find(([path]) => String(path).includes("/preflight"));
    expect(preflightCall?.[2]).toMatchObject({ method: "POST", body: { all_eligible: true, partition_keys: [] } });
    expect(apiFetch.mock.calls.some(([path]) => String(path).includes("/apply"))).toBe(false);
  });

  it("lists archived partitions separately for restore", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: /Archived/ }));
    await waitFor(() => expect(screen.getByText("event_date=2023-12-31")).toBeInTheDocument());
    expect(screen.getByText("Standard-IA")).toBeInTheDocument();
  });

  it("viewers see no archive actions", async () => {
    role = "viewer";
    renderPanel();
    const button = await screen.findByRole("button", { name: "Archive all suggested…" });
    expect(button).toBeDisabled();
  });
});
