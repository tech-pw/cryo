import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SpecMigrationRead } from "../../../api/types";
import { migrationProgress, SpecMigrationCard } from "./SpecMigrationCard";

const client = vi.hoisted(() => ({
  getTableSpecMigration: vi.fn(),
  requestSpecMigrationApproval: vi.fn().mockResolvedValue({}),
  runTableOperations: vi.fn().mockResolvedValue({ run_id: "run-1" })
}));
const writeAccess = vi.hoisted(() => ({ allowed: true, reason: null as string | null, hidden: false }));

vi.mock("../../../api/client", () => client);
vi.mock("../../../lib/auth", () => ({
  useAuth: () => ({ api: { token: "test-token" }, token: "test-token", role: "operator" })
}));
vi.mock("../../../lib/useWriteControl", () => ({ useWriteControl: () => writeAccess }));

const migration = (overrides: Partial<SpecMigrationRead> = {}): SpecMigrationRead => ({
  table_id: "table-1",
  scanned_at: "2026-11-01T02:00:00Z",
  recorded: true,
  default_spec_id: 1,
  old_spec_partitions: 30,
  old_spec_files: 300,
  old_spec_bytes: 12 * 2 ** 30,
  baseline_old_spec_partitions: null,
  destination_engine: "spark_emr",
  engine_supported: true,
  engine_note: null,
  approval: null,
  last_run: null,
  ...overrides
});

const renderCard = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <SpecMigrationCard tableId="table-1" />
      </MemoryRouter>
    </QueryClientProvider>
  );

describe("SpecMigrationCard", () => {
  beforeEach(() => {
    writeAccess.allowed = true;
    writeAccess.reason = null;
    client.requestSpecMigrationApproval.mockClear();
    client.runTableOperations.mockClear();
  });

  it("explains old-spec data and requests an approval", async () => {
    client.getTableSpecMigration.mockResolvedValue(migration());
    renderCard();

    expect(await screen.findByRole("heading", { name: "Migrate to current partition spec" })).toBeInTheDocument();
    expect(screen.getByText(/30 partitions \(300 data files/)).toBeInTheDocument();
    expect(screen.getByText("Not requested")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Request approval" }));
    await waitFor(() => expect(client.requestSpecMigrationApproval).toHaveBeenCalledWith({ token: "test-token" }, "table-1"));
    expect(client.runTableOperations).not.toHaveBeenCalled();
  });

  it("waits while the request is pending", async () => {
    client.getTableSpecMigration.mockResolvedValue(
      migration({ approval: { request_id: "req-1", status: "pending", requested_by: "op" } })
    );
    renderCard();

    expect(await screen.findByRole("button", { name: "Waiting for approval" })).toBeDisabled();
    expect(screen.getByRole("link", { name: /Review in Decisions/ })).toHaveAttribute(
      "href",
      "/decisions?tab=approvals#operation-approvals"
    );
  });

  it("runs the migration once approved and shows progress against the baseline", async () => {
    client.getTableSpecMigration.mockResolvedValue(
      migration({
        old_spec_partitions: 10,
        baseline_old_spec_partitions: 40,
        approval: { request_id: "req-1", status: "approved", requested_by: "op", approved_by: "editor" }
      })
    );
    renderCard();

    expect(await screen.findByRole("progressbar", { name: "75% of old-spec partitions migrated" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Run migration" }));
    await waitFor(() =>
      expect(client.runTableOperations).toHaveBeenCalledWith({ token: "test-token" }, "table-1", {
        operations: ["migrate_partition_spec"],
        trigger: "web-spec-migration"
      })
    );
  });

  it("blocks a Trino destination with the reason", async () => {
    const note = "Trino cannot filter partitions of an older spec.";
    client.getTableSpecMigration.mockResolvedValue(
      migration({ destination_engine: "trino", engine_supported: false, engine_note: note })
    );
    renderCard();

    expect(await screen.findByText(note)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Request approval" })).toBeDisabled();
  });

  it("renders nothing for a table with no old-spec data or scan record", async () => {
    client.getTableSpecMigration.mockResolvedValue(migration({ old_spec_partitions: 0 }));
    const { container } = renderCard();
    await waitFor(() => expect(client.getTableSpecMigration).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("computes progress only against a baseline", () => {
    expect(migrationProgress({ baseline_old_spec_partitions: null, old_spec_partitions: 5 })).toBeNull();
    expect(migrationProgress({ baseline_old_spec_partitions: 4, old_spec_partitions: 6 })).toBe(0);
    expect(migrationProgress({ baseline_old_spec_partitions: 4, old_spec_partitions: 0 })).toBe(100);
  });
});
