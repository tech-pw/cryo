import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { listDestinations, listPresets, runMaintenance } from "../../api/client";
import { PageHeader } from "../ui/page-header";
import { Select } from "../ui/select";
import { useAuth } from "../../lib/auth";
import {
  BATCH_RECIPES,
  loadDraft,
  MAINTENANCE_BATCH_CHANGE_EVENT,
  setBatchDestination,
  setBatchPreset
} from "../../lib/maintenanceBatch";
import { BatchPanel } from "./BatchPanel";

export const BatchReviewPage = () => {
  const { api, subject, token } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const identity = useMemo(
    () => ({
      installationId: "local",
      principalId: subject || "anonymous",
      tenantId: "local"
    }),
    [subject]
  );

  const destinations = useQuery({
    queryKey: ["destinations", token],
    queryFn: () => listDestinations(api)
  });
  const presets = useQuery({
    queryKey: ["presets", token],
    queryFn: () => listPresets(api)
  });

  const [draft, setDraft] = useState(() => loadDraft(identity));
  useEffect(() => {
    const reload = () => setDraft(loadDraft(identity));
    window.addEventListener(MAINTENANCE_BATCH_CHANGE_EVENT, reload);
    return () => window.removeEventListener(MAINTENANCE_BATCH_CHANGE_EVENT, reload);
  }, [identity]);
  const presetOptions = (presets.data ?? [])
    .filter((preset) => !draft.destinationId || preset.destination_id === draft.destinationId)
    .map((preset) => ({ label: preset.name, value: preset.id }));
  const selectedPreset = presets.data?.find((preset) => preset.id === draft.presetId);
  const selectedDestination = destinations.data?.find((destination) => destination.id === draft.destinationId);

  const run = useMutation({
    mutationFn: (batch: MaintenanceBatchDraft) => {
      if (!batch.destinationId || !batch.presetId) {
        throw new Error("Select a destination and preset before running the batch.");
      }
      const preset = presets.data?.find((item) => item.id === batch.presetId);
      return runMaintenance(api, {
        table_ids: batch.tables.map((table) => table.id),
        destination_id: batch.destinationId,
        preset_id: batch.presetId,
        mode: preset?.mode ?? "standard",
        trigger: "web-batch"
      });
    },
    onSuccess: (result) => {
      toast.success(`Run queued: ${result.run_id}`);
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      navigate("/runs?tab=history");
    },
    onError: (error) => toast.error((error as Error).message)
  });

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Maintenance batch"
        subtitle="Review the exact tables and recipe, then run maintenance."
        rootCrumb={{ label: "Runs", to: "/runs" }}
      />
      <div className="flex flex-wrap items-center gap-3 rounded-[var(--radius-default)] border border-border p-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-muted-foreground">Destination</span>
          <Select
            aria-label="Batch destination"
            className="h-8 w-[220px] text-xs"
            allowClear
            placeholder="Select destination"
            value={draft.destinationId ?? undefined}
            disabled={destinations.isLoading}
            onChange={(destinationId) => {
              const next = setBatchDestination(identity, destinationId ?? null);
              const preset = presets.data?.find((item) => item.id === next.presetId);
              if (preset && destinationId && preset.destination_id !== destinationId) {
                setDraft(setBatchPreset(identity, null));
                return;
              }
              setDraft(next);
            }}
            options={(destinations.data ?? []).map((destination) => ({
              label: destination.name,
              value: destination.id
            }))}
          />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-muted-foreground">Preset</span>
          <Select
            aria-label="Batch preset"
            className="h-8 w-[220px] text-xs"
            allowClear
            placeholder="Select preset"
            value={draft.presetId ?? undefined}
            disabled={presets.isLoading}
            onChange={(presetId) => setDraft(setBatchPreset(identity, presetId ?? null))}
            options={presetOptions}
          />
        </div>
        {selectedPreset && selectedDestination && selectedPreset.destination_id !== selectedDestination.id ? (
          <span className="text-xs text-[var(--color-status-warning)]">
            {selectedPreset.name} targets a different destination.
          </span>
        ) : null}
      </div>
      <BatchPanel
        identity={identity}
        draft={draft}
        readyCount={draft.tables.length}
        destinationLabel={selectedDestination?.name}
        presetLabel={selectedPreset?.name}
        onRun={(batch) => run.mutate(batch)}
      />
      <p className="text-sm text-muted-foreground">
        Recipe runs {BATCH_RECIPES[draft.recipe].label.toLowerCase()}. After submit, status is on{" "}
        <Link to="/runs?tab=history">Runs</Link>. Accepted work still waits for admission.
      </p>
    </div>
  );
};
