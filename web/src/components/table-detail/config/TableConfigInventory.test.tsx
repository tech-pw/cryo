import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ConfigPreviewResponse,
  MaintenanceOperationRead,
  TableConfigResponse,
  TableOperationsRead,
  TableSettingRead
} from "../../../api/types";
import { CryoTooltipProvider } from "../../ui/tooltip";
import { TableConfigInventory } from "./TableConfigInventory";

const access = vi.hoisted(() => ({
  operator: { allowed: true, hidden: false, reason: null as string | null },
  admin: { allowed: false, hidden: false, reason: "Admin role is required for this action." as string | null }
}));

vi.mock("../../../lib/useWriteControl", () => ({
  useWriteControl: (action: string) => (action === "editTableConfigAdmin" ? access.admin : access.operator)
}));

vi.mock("../../../lib/auth", () => ({
  useAuth: () => ({ api: { token: "t" }, token: "t", role: "operator" })
}));

const client = vi.hoisted(() => ({
  getTableConfig: vi.fn(),
  previewTableConfig: vi.fn(),
  applyTableConfig: vi.fn(),
  putOperationExclusions: vi.fn()
}));

vi.mock("../../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../api/client")>();
  return { ...actual, ...client };
});

const setting = (overrides: Partial<TableSettingRead>): TableSettingRead => ({
  key: "k",
  label: "Label",
  group: "file_layout",
  kind: "property",
  summary: "What it does.",
  takes_effect: "Next write.",
  reversible: "Yes.",
  doc_url: "https://iceberg.apache.org/docs/latest/configuration/",
  current_value: null,
  current_source: "iceberg_default",
  is_set: false,
  default_value: null,
  default_label: null,
  recommended_value: null,
  recommendation_reason: null,
  note: null,
  matches_recommendation: null,
  change_risk: "low",
  recommendation_risk: null,
  risk_directions: [],
  editable: true,
  read_only_reason: null,
  min_role: "operator",
  editor: { value_type: "text", options: [], minimum: null, maximum: null, unit: null, allow_unset: true },
  engine_notes: [],
  ...overrides
});

const targetSize = setting({
  key: "write.target-file-size-bytes",
  label: "Target data file size",
  current_value: "536870912",
  default_value: "536870912",
  recommended_value: "268435456",
  recommendation_reason: "Matches the preset's 256 MiB compaction target.",
  matches_recommendation: false,
  change_risk: "medium",
  recommendation_risk: "low",
  risk_directions: [
    { label: "Lower", risk: "medium", detail: "More, smaller files.", blocked: null },
    { label: "Raise", risk: "low", detail: null, blocked: null }
  ],
  editor: { value_type: "bytes", options: [], minimum: 16_777_216, maximum: 4_294_967_296, unit: null, allow_unset: true },
  engine_notes: [{ engine: "trino", note: "Trino 482+ honours this property." }]
});

const gc = setting({
  key: "gc.enabled",
  label: "Garbage collection",
  group: "retention",
  current_value: "true",
  current_source: "table",
  is_set: true,
  default_value: "true",
  change_risk: "high",
  editor: { value_type: "bool", options: [], minimum: null, maximum: null, unit: null, allow_unset: true }
});

const formatVersion = setting({
  key: "format-version",
  label: "Format version",
  group: "format",
  kind: "format_version",
  current_value: "2",
  current_source: "metadata",
  is_set: true,
  min_role: "admin",
  change_risk: "high",
  editor: { value_type: "enum", options: ["1", "2", "3"], minimum: null, maximum: null, unit: null, allow_unset: false }
});

const dataPath = setting({
  key: "write.data.path",
  label: "Data file location",
  editable: false,
  read_only_reason: "Moving new data files away from the table location breaks orphan cleanup.",
  change_risk: "high"
});

const operation = (overrides: Partial<MaintenanceOperationRead>): MaintenanceOperationRead => ({
  operation: "optimize",
  label: "Compact small files",
  phase: "compaction",
  summary: "Rewrites small data files.",
  when_planned: "Standard and deep modes.",
  exclude_consequence: "Small files keep accumulating.",
  exclude_risk: "medium",
  reasons_to_exclude: ["The writer already compacts."],
  companion_hint: null,
  companion_operation: null,
  engine_notes: [{ engine: "trino", note: "ALTER TABLE ... EXECUTE optimize." }],
  doc_url: "https://iceberg.apache.org/docs/latest/maintenance/#compact-data-files",
  excluded: false,
  excluded_by: null,
  excluded_at: null,
  excluded_reason: null,
  status: "when_needed",
  status_detail: "Runs when the scan finds work for it.",
  ...overrides
});

const operations = (ops: Partial<MaintenanceOperationRead>[] = []): TableOperationsRead => ({
  preset_name: "standard",
  mode: "deep",
  held: false,
  operations: [
    operation({
      operation: "convert_deletes",
      label: "Apply delete files",
      status: "when_needed"
    }),
    operation({
      companion_hint: "Apply delete files also rewrites data files.",
      companion_operation: "convert_deletes"
    }),
    operation({
      operation: "expire_snapshots",
      label: "Expire snapshots",
      phase: "cleanup",
      exclude_risk: "high",
      exclude_consequence: "Storage grows with every commit.",
      status: "runs",
      status_detail: "Runs on every standard run."
    }),
    operation({
      operation: "remove_orphan_files",
      label: "Remove orphan files",
      phase: "cleanup",
      exclude_risk: "low",
      excluded: true,
      excluded_by: "ops@pw",
      excluded_reason: "shared location",
      status: "excluded",
      status_detail: "Excluded for this table. No run plans it."
    })
  ].map((op) => ({ ...op, ...(ops.find((o) => o.operation === op.operation) ?? {}) }))
});

const response = (overrides: Partial<TableConfigResponse> = {}): TableConfigResponse => ({
  table_id: "table-1",
  read_from: "catalog",
  read_at: "2026-10-17T07:00:00Z",
  catalog_error: null,
  can_edit: true,
  preset_name: "standard",
  preset_engine: "spark_emr",
  groups: [
    { id: "file_layout", label: "File layout", description: "How data files are written." },
    { id: "retention", label: "Retention and GC", description: "What expiry may delete." },
    { id: "format", label: "Format and identity", description: "Table format." }
  ],
  settings: [targetSize, dataPath, gc, formatVersion],
  metadata: {
    format_version: 2,
    table_uuid: "uuid-1",
    location: "s3://bucket/db/events",
    metadata_location: "s3://bucket/db/events/metadata/00012.metadata.json",
    last_updated_ms: 1_760_000_000_000,
    current_snapshot_id: "42",
    snapshot_count: 12,
    metadata_log_count: 8,
    schema_id: 1,
    schema_count: 2,
    column_count: 5,
    identifier_fields: [],
    partition_fields: [{ name: "event_date_day", source_column: "event_date", transform: "day" }],
    spec_id: 0,
    spec_count: 1,
    sort_fields: [],
    sort_order_id: 0,
    refs: [
      { name: "main", type: "branch", snapshot_id: "42", retained_forever: false },
      { name: "audit-2026", type: "tag", snapshot_id: "7", retained_forever: true }
    ],
    statistics_files: 0,
    partition_statistics_files: 0
  },
  properties: { "gc.enabled": "true", "write.format.default": "parquet" },
  property_details: [
    {
      key: "gc.enabled",
      value: "true",
      kind: "setting",
      summary: "Lets maintenance delete files.",
      doc_url: "https://iceberg.apache.org/docs/latest/configuration/",
      setting_key: "gc.enabled"
    },
    {
      key: "write.format.default",
      value: "parquet",
      kind: "write",
      summary: "File format for new data files.",
      doc_url: "https://iceberg.apache.org/docs/latest/configuration/#write-properties",
      setting_key: null
    }
  ],
  columns: ["id", "event_date"],
  operations: operations(),
  ...overrides
});

const previewFor = (risk: "low" | "medium" | "high", key = "write.target-file-size-bytes"): ConfigPreviewResponse => ({
  changes: [
    {
      key,
      label: key,
      kind: "property",
      old_value: "536870912",
      new_value: "268435456",
      risk,
      warnings: risk === "high" ? ["Expiry may now delete snapshots."] : [],
      blocked: null,
      min_role: "operator",
      no_op: false
    }
  ],
  overall_risk: risk,
  requires_confirmation: risk === "high",
  blocked: false
});

const renderInventory = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <CryoTooltipProvider>
        <TableConfigInventory tableId="table-1" />
      </CryoTooltipProvider>
    </QueryClientProvider>
  );
};

describe("TableConfigInventory", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    access.operator = { allowed: true, hidden: false, reason: null };
    access.admin = { allowed: false, hidden: false, reason: "Admin role is required for this action." };
    client.getTableConfig.mockResolvedValue(response());
    client.previewTableConfig.mockResolvedValue(previewFor("low"));
  });

  it("shows value, source, recommendation and risk for each setting", async () => {
    renderInventory();
    const row = await screen.findByTestId("setting-write.target-file-size-bytes");
    expect(within(row).getByText("512 MiB")).toBeInTheDocument();
    expect(within(row).getByText("Iceberg default")).toBeInTheDocument();
    expect(within(row).getByText("256 MiB")).toBeInTheDocument();
    expect(within(row).getByText("Matches the preset's 256 MiB compaction target.")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: /Medium risk/ })).toBeInTheDocument();
    expect(screen.getByText("Live from the catalog", { exact: false })).toBeInTheDocument();
  });

  it("explains risk per direction and per engine in the info popover", async () => {
    renderInventory();
    const row = await screen.findByTestId("setting-write.target-file-size-bytes");
    fireEvent.click(within(row).getByRole("button", { name: /Medium risk/ }));
    expect(await screen.findByText("More, smaller files.")).toBeInTheDocument();
    expect(screen.getByText("Trino 482+ honours this property.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Documentation/ })).toHaveAttribute(
      "href",
      "https://iceberg.apache.org/docs/latest/configuration/"
    );
  });

  it("filters to settings that differ from the recommendation", async () => {
    renderInventory();
    await screen.findByTestId("setting-gc.enabled");
    fireEvent.click(screen.getByRole("button", { name: "Differs (1)" }));
    expect(screen.getByTestId("setting-write.target-file-size-bytes")).toBeInTheDocument();
    expect(screen.queryByTestId("setting-gc.enabled")).not.toBeInTheDocument();
  });

  it("locks read-only settings with the reason and gates admin settings", async () => {
    renderInventory();
    const pathRow = await screen.findByTestId("setting-write.data.path");
    expect(within(pathRow).queryByRole("button", { name: /Edit/ })).not.toBeInTheDocument();
    expect(within(pathRow).getByLabelText(/Read-only: Moving new data files/)).toBeInTheDocument();
    const formatRow = screen.getByTestId("setting-format-version");
    expect(within(formatRow).getByRole("button", { name: "Edit Format version" })).toBeDisabled();
  });

  it("stages a recommendation, previews it, and applies it in one call", async () => {
    client.applyTableConfig.mockResolvedValue({
      applied: previewFor("low").changes,
      audit_log_ids: [1],
      message: "Applied 1 change",
      config: response()
    });
    renderInventory();
    const row = await screen.findByTestId("setting-write.target-file-size-bytes");
    fireEvent.click(within(row).getByRole("button", { name: /Stage recommendation/ }));

    const bar = await screen.findByRole("region", { name: "Staged configuration changes" });
    expect(within(bar).getByText("1 change staged", { exact: false })).toBeInTheDocument();
    await waitFor(() =>
      expect(client.previewTableConfig).toHaveBeenCalledWith(expect.anything(), "table-1", [
        {
          key: "write.target-file-size-bytes",
          value: "268435456",
          expected_value: "536870912",
          check_expected: true
        }
      ])
    );

    fireEvent.click(within(bar).getByRole("button", { name: "Review and apply" }));
    const apply = await screen.findByRole("button", { name: "Apply change" });
    await waitFor(() => expect(apply).toBeEnabled());
    fireEvent.click(apply);
    await waitFor(() => expect(client.applyTableConfig).toHaveBeenCalledTimes(1));
    expect(client.applyTableConfig.mock.calls[0][2]).toMatchObject({ confirm: false, reason: null });
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Staged configuration changes" })).not.toBeInTheDocument()
    );
  });

  it("requires acknowledgement and a reason before a high-risk apply", async () => {
    client.previewTableConfig.mockResolvedValue(previewFor("high", "gc.enabled"));
    client.applyTableConfig.mockResolvedValue({
      applied: [],
      audit_log_ids: [2],
      message: "Applied 1 change",
      config: response()
    });
    renderInventory();
    const row = await screen.findByTestId("setting-gc.enabled");
    fireEvent.click(within(row).getByRole("button", { name: "Edit Garbage collection" }));
    fireEvent.click(within(row).getByRole("switch", { name: "Garbage collection" }));
    fireEvent.click(within(row).getByRole("button", { name: "Stage change" }));

    fireEvent.click(await screen.findByRole("button", { name: "Review and apply" }));
    const apply = await screen.findByRole("button", { name: "Apply change" });
    expect(await screen.findByText("Expiry may now delete snapshots.", { selector: ".tc-review *" })).toBeInTheDocument();
    expect(apply).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox", { name: "I understand the consequences" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Reason for this change" }), {
      target: { value: "Re-enable expiry after the backfill" }
    });
    await waitFor(() => expect(apply).toBeEnabled());
    fireEvent.click(apply);
    await waitFor(() => expect(client.applyTableConfig).toHaveBeenCalledTimes(1));
    expect(client.applyTableConfig.mock.calls[0][2]).toMatchObject({
      changes: [{ key: "gc.enabled", value: "false", expected_value: "true", check_expected: true }],
      confirm: true,
      reason: "Re-enable expiry after the backfill"
    });
  });

  it("pauses editing when only the scan snapshot is available", async () => {
    client.getTableConfig.mockResolvedValue(
      response({ read_from: "scan", catalog_error: "timeout", metadata: null, can_edit: false })
    );
    renderInventory();
    expect(await screen.findByText("Showing the last scan, not the live table.")).toBeInTheDocument();
    const row = screen.getByTestId("setting-gc.enabled");
    expect(within(row).queryByRole("button", { name: /Edit/ })).not.toBeInTheDocument();
  });

  it("shows metadata, refs kept forever, and the raw properties", async () => {
    renderInventory();
    expect(await screen.findByText("day(event_date)")).toBeInTheDocument();
    expect(screen.getByText("1 ref pins snapshots forever")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "All table properties (2)" }));
    expect(screen.getByText("write.format.default")).toBeInTheDocument();
  });

  it("disables editing for viewers with the reason", async () => {
    access.operator = { allowed: false, hidden: false, reason: "Viewer access is read-only." };
    renderInventory();
    const row = await screen.findByTestId("setting-gc.enabled");
    const edit = within(row).getByRole("button", { name: "Edit Garbage collection" });
    expect(edit).toBeDisabled();
    expect(edit).toHaveAttribute("title", "Viewer access is read-only.");
  });

  it("explains every setting from an info button next to its name", async () => {
    renderInventory();
    const row = await screen.findByTestId("setting-write.target-file-size-bytes");
    fireEvent.click(within(row).getByRole("button", { name: "About Target data file size" }));
    expect(await screen.findByText("More, smaller files.")).toBeInTheDocument();
  });

  it("explains every raw property and links curated ones to their setting", async () => {
    renderInventory();
    fireEvent.click(await screen.findByRole("button", { name: "All table properties (2)" }));
    expect(screen.getByText("File format for new data files.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "About write.format.default" }));
    expect(await screen.findByRole("link", { name: /Iceberg documentation/ })).toHaveAttribute(
      "href",
      "https://iceberg.apache.org/docs/latest/configuration/#write-properties"
    );
    fireEvent.click(screen.getByRole("button", { name: "About gc.enabled" }));
    expect(await screen.findByRole("button", { name: /Show its setting/ })).toBeInTheDocument();
  });

  it("shows what each maintenance operation does for this table", async () => {
    renderInventory();
    const optimize = await screen.findByTestId("operation-optimize");
    expect(within(optimize).getByText("When needed")).toBeInTheDocument();
    expect(within(optimize).getByRole("switch", { name: "Run Compact small files" })).toBeChecked();
    const orphan = screen.getByTestId("operation-remove_orphan_files");
    expect(within(orphan).getByText("Excluded")).toBeInTheDocument();
    expect(within(orphan).getByText(/ops@pw/)).toBeInTheDocument();
    expect(within(orphan).getByRole("switch", { name: "Run Remove orphan files" })).not.toBeChecked();
    fireEvent.click(within(optimize).getByRole("button", { name: "About Compact small files" }));
    expect(await screen.findByText("The writer already compacts.")).toBeInTheDocument();
  });

  it("excludes an operation with a reason and warns about its companion", async () => {
    client.putOperationExclusions.mockResolvedValue({
      added: ["optimize"],
      removed: [],
      audit_log_ids: [9],
      message: "Excluded Compact small files.",
      operations: operations([{ operation: "optimize", excluded: true, status: "excluded" }])
    });
    renderInventory();
    const optimize = await screen.findByTestId("operation-optimize");
    fireEvent.click(within(optimize).getByRole("switch", { name: "Run Compact small files" }));
    expect(within(optimize).getByText("Will be excluded")).toBeInTheDocument();
    expect(screen.getByText("Apply delete files also rewrites data files.")).toBeInTheDocument();

    const bar = screen.getByRole("region", { name: "Staged operation changes" });
    fireEvent.click(within(bar).getByRole("button", { name: "Review and save" }));
    const save = await screen.findByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Reason for this change" }), {
      target: { value: "Writer compacts on commit" }
    });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);
    await waitFor(() => expect(client.putOperationExclusions).toHaveBeenCalledTimes(1));
    const body = client.putOperationExclusions.mock.calls[0][2];
    expect([...body.excluded].sort()).toEqual(["optimize", "remove_orphan_files"]);
    expect(body).toMatchObject({ reason: "Writer compacts on commit", confirm: false });
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Staged operation changes" })).not.toBeInTheDocument()
    );
  });

  it("needs acknowledgement before excluding a high-risk operation", async () => {
    client.putOperationExclusions.mockResolvedValue({
      added: ["expire_snapshots"],
      removed: [],
      audit_log_ids: [10],
      message: "Excluded Expire snapshots.",
      operations: operations()
    });
    renderInventory();
    const expire = await screen.findByTestId("operation-expire_snapshots");
    fireEvent.click(within(expire).getByRole("switch", { name: "Run Expire snapshots" }));
    fireEvent.click(screen.getByRole("button", { name: "Review and save" }));
    fireEvent.change(await screen.findByRole("textbox", { name: "Reason for this change" }), {
      target: { value: "Legal hold on this table" }
    });
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "I understand the consequences" }));
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);
    await waitFor(() => expect(client.putOperationExclusions).toHaveBeenCalledTimes(1));
    expect(client.putOperationExclusions.mock.calls[0][2]).toMatchObject({ confirm: true });
  });

  it("keeps operation toggles read-only for viewers", async () => {
    access.operator = { allowed: false, hidden: false, reason: "Viewer access is read-only." };
    renderInventory();
    const optimize = await screen.findByTestId("operation-optimize");
    expect(within(optimize).getByRole("switch", { name: "Run Compact small files" })).toBeDisabled();
  });
});
