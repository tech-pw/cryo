import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ControlRoom } from "./ControlRoom";

vi.mock("../lib/auth", () => ({
  useAuth: () => ({
    api: { token: "test-token" },
    token: "test-token",
    role: "admin",
    subject: "operator@example.com"
  })
}));

vi.mock("../api/client", () => ({
  getMaintenanceSafetySummary: vi.fn().mockResolvedValue({
    intents_by_status: { reconcile_required: 1 },
    effects_by_state: { unknown: 1 },
    ambiguous_effects: 1,
    active_attempts: 1,
    active_pauses: 0
  }),
  listMaintenanceControls: vi.fn().mockResolvedValue([]),
  listMaintenanceEffects: vi.fn().mockResolvedValue([
    {
      id: "effect-1",
      intent_id: "intent-1",
      job_id: "job-1",
      table_id: "table-1",
      table_ref: "iceberg.analytics.events",
      destination_id: "destination-1",
      step_index: 0,
      operation_type: "optimize",
      strategy: "stable_reference_probe",
      state: "unknown",
      external_reference: "query-42",
      probe_count: 3,
      next_probe_at: null,
      submitted_at: "2026-08-04T10:00:00Z",
      resolved_at: null,
      error: { message: "coordinator response lost" },
      created_at: "2026-08-04T10:00:00Z",
      updated_at: "2026-08-04T10:01:00Z"
    }
  ]),
  listMaintenanceIntents: vi.fn().mockResolvedValue([
    {
      id: "intent-1",
      job_id: "job-1",
      table_id: "table-1",
      table_ref: "iceberg.analytics.events",
      destination_id: "destination-1",
      version: 2,
      status: "reconcile_required",
      actor_type: "api",
      actor_id: "operator@example.com",
      operation_scope: ["optimize"],
      plan_hash: "a".repeat(64),
      fencing_epoch: 7,
      containment_action: "reconcile",
      expires_at: "2026-08-04T12:00:00Z",
      created_at: "2026-08-04T10:00:00Z",
      updated_at: "2026-08-04T10:01:00Z"
    }
  ]),
  pauseMaintenance: vi.fn().mockResolvedValue({
    scope_type: "global",
    scope_id: "global",
    paused: true,
    generation: 1,
    reason: "incident containment",
    changed_by: "operator@example.com",
    updated_at: "2026-08-04T10:02:00Z"
  }),
  resolveMaintenanceEffect: vi.fn().mockResolvedValue({
    receipt: {
      id: "effect-1",
      state: "succeeded"
    },
    continuation: "queued_for_admission",
    message: "Outcome recorded; remaining work is queued for Governor admission."
  }),
  resolveMaintenanceEffectsBatch: vi.fn().mockResolvedValue({
    requested: 1,
    resolved: 1,
    failed: 0,
    items: []
  }),
  resumeMaintenance: vi.fn()
}));

import {
  pauseMaintenance,
  resolveMaintenanceEffect,
  resolveMaintenanceEffectsBatch
} from "../api/client";

const renderPage = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } }
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ControlRoom />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("Control Room", () => {
  it("surfaces contained uncertainty and intent lineage", async () => {
    renderPage();

    expect(screen.getByRole("heading", { name: "Control Room" })).toBeInTheDocument();
    expect(await screen.findByText("Contained uncertainty")).toBeInTheDocument();
    expect(screen.getByText("query-42")).toBeInTheDocument();
    expect(screen.getByText("v2 / fence 7")).toBeInTheDocument();
  });

  it("requires a reason before engaging the global hold", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Emergency stop" }));

    const confirm = screen.getByRole("button", { name: "Hold all maintenance" });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Operator reason"), {
      target: { value: "incident containment" }
    });
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(pauseMaintenance).toHaveBeenCalledWith(
        { token: "test-token" },
        {
          scope_type: "global",
          scope_id: "global",
          reason: "incident containment"
        }
      )
    );
  });

  it("records operator evidence before continuing an ambiguous effect", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Resolve" }));

    const confirm = screen.getByRole("button", { name: "Record outcome" });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Evidence and verification"), {
      target: { value: "Verified query-42 in engine history" }
    });
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(resolveMaintenanceEffect).toHaveBeenCalledWith(
        { token: "test-token" },
        "effect-1",
        {
          outcome: "succeeded",
          reason: "Verified query-42 in engine history",
          evidence_reference: "query-42",
          continue_job: true
        }
      )
    );
  });

  it("records one attestation across explicitly selected receipts", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select row" }));
    fireEvent.click(screen.getByRole("button", { name: "Attest selected (1)" }));
    fireEvent.change(screen.getByLabelText("Shared evidence and verification"), {
      target: { value: "Verified selected receipts in engine history" }
    });
    fireEvent.click(screen.getByRole("button", { name: "Record 1 outcomes" }));

    await waitFor(() =>
      expect(resolveMaintenanceEffectsBatch).toHaveBeenCalledWith(
        { token: "test-token" },
        {
          receipt_ids: ["effect-1"],
          outcome: "succeeded",
          reason: "Verified selected receipts in engine history",
          continue_job: true
        }
      )
    );
  });

  it("can contain one destination without stopping the fleet", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Hold destination" }));
    fireEvent.change(screen.getByLabelText("Containment reason"), {
      target: { value: "canary failure containment" }
    });
    const confirmButtons = screen.getAllByRole("button", { name: "Hold destination" });
    fireEvent.click(confirmButtons.at(-1)!);

    await waitFor(() =>
      expect(pauseMaintenance).toHaveBeenCalledWith(
        { token: "test-token" },
        {
          scope_type: "destination",
          scope_id: "destination-1",
          reason: "canary failure containment"
        }
      )
    );
  });
});
