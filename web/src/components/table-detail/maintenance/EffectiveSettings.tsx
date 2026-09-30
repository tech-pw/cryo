import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import {
  assignTables,
  getEffectiveParams,
  getEffectivePreset,
  listCompatiblePresets,
  listDestinations,
  listPresets,
  patchTableParamOverrides,
  unassignTables
} from "../../../api/client";
import { QueryErrorAlert } from "../../common";
import { provenanceLabel } from "../helpers";
import { Button } from "../../ui/button";
import { Input } from "../../ui/input";
import { Select } from "../../ui/select";
import { useAuth } from "../../../lib/auth";
import { ADMIN_REASON, VIEWER_REASON } from "../../../lib/permissions";
import { useWriteControl } from "../../../lib/useWriteControl";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import "./maintenance-details.css";

const formatParamValue = (value: unknown): string => {
  if (value === null || value === undefined || value === "") return "not reported";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "not reported";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
};

const provenanceText = (value?: string | null): string => {
  if (!value) return "not recorded";
  if (value === "rule_after_stale_pin") return "rule after a stale pin";
  const label = provenanceLabel(value);
  return label === "unknown" ? "not recorded" : label;
};

const parseOverrideValue = (raw: string): unknown => {
  const trimmed = raw.trim();
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  if (trimmed !== "" && !Number.isNaN(Number(trimmed))) return Number(trimmed);
  return raw;
};

export const EffectiveSettings = ({ tableId }: { tableId: string }) => {
  const { api, token, role } = useAuth();
  const queryClient = useQueryClient();
  const assignAccess = useWriteControl("assignPreset");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [overrideKey, setOverrideKey] = useState("");
  const [overrideValue, setOverrideValue] = useState("");
  const enabled = tableId.length > 0;
  const pickerEnabled = pickerOpen && enabled && assignAccess.allowed;
  // PATCH /param-overrides is require_admin. No WriteAction covers overrides.
  const canOverride = role === "admin";
  const overrideReason = canOverride ? null : role === "viewer" ? VIEWER_REASON : ADMIN_REASON;

  const preset = useQuery({
    queryKey: tableKeys.effectivePreset(tableId, token),
    queryFn: () => getEffectivePreset(api, tableId),
    enabled,
    retry: false
  });
  const params = useQuery({
    queryKey: tableKeys.effectiveParams(tableId, token),
    queryFn: () => getEffectiveParams(api, tableId),
    enabled,
    retry: false
  });
  const presets = useQuery({
    queryKey: tableKeys.presets(token),
    queryFn: () => listPresets(api),
    enabled: pickerEnabled,
    retry: false
  });
  const destinations = useQuery({
    queryKey: tableKeys.destinations(token),
    queryFn: () => listDestinations(api),
    enabled: pickerEnabled,
    retry: false
  });
  const compatible = useQuery({
    queryKey: tableKeys.compatiblePresets(tableId, token),
    queryFn: () => listCompatiblePresets(api, tableId),
    enabled: pickerEnabled,
    retry: false
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: tableKeys.effectivePreset(tableId, token) });
    void queryClient.invalidateQueries({ queryKey: tableKeys.effectiveParams(tableId, token) });
  };
  const assign = useMutation({
    mutationFn: (presetId: string) =>
      assignTables(api, { selection: { mode: "ids", ids: [tableId] }, preset_id: presetId }),
    onSuccess: () => {
      toast.success("Preset assigned");
      invalidate();
    },
    onError: (error) => toast.error((error as Error).message)
  });
  const unassign = useMutation({
    mutationFn: () => unassignTables(api, { selection: { mode: "ids", ids: [tableId] } }),
    onSuccess: () => {
      toast.success("Preset unassigned");
      invalidate();
    },
    onError: (error) => toast.error((error as Error).message)
  });
  const patch = useMutation({
    mutationFn: (overrides: Record<string, unknown>) => patchTableParamOverrides(api, tableId, overrides),
    onSuccess: () => {
      toast.success("Parameter overrides updated");
      setOverrideKey("");
      setOverrideValue("");
      invalidate();
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const effective = params.data?.effective_params ?? {};
  const presetParams = params.data?.preset_params ?? {};
  const overrides = params.data?.param_overrides ?? {};
  const entries = [...new Set([...Object.keys(effective), ...Object.keys(presetParams), ...Object.keys(overrides)])].map(
    (key) => [key, key in effective ? effective[key] : key in overrides ? overrides[key] : presetParams[key]] as const
  );
  const currentId = preset.data?.preset_id ?? undefined;
  const options = (compatible.data ?? []).map((item) => {
    const destination = destinations.data?.find((row) => row.id === item.destination_id);
    return {
      value: item.id,
      label: destination ? `${item.name} · ${destination.name}` : item.name
    };
  });
  const currentMissing =
    currentId && !options.some((option) => option.value === currentId)
      ? presets.data?.find((item) => item.id === currentId)
      : undefined;
  const presetOptions = currentMissing
    ? [{ value: currentMissing.id, label: `${currentMissing.name} (catalog not linked)` }, ...options]
    : options;

  const addOverride = () => {
    if (!canOverride) return;
    const key = overrideKey.trim();
    if (!key || overrideValue.trim() === "") return;
    patch.mutate({ ...overrides, [key]: parseOverrideValue(overrideValue) });
  };

  return (
    <section id="maintenance-settings" className="td-maintenance-settings td-mt-card" aria-labelledby="maintenance-settings-heading">
      <h3 id="maintenance-settings-heading" className="mb-2 text-sm font-semibold text-foreground">
        Effective settings
      </h3>
      <p className="td-maintenance-intro">The workflow and parameters that govern this table's maintenance.</p>
      <QueryErrorAlert error={preset.error} title="Couldn't load workflow" />
      <QueryErrorAlert error={params.error} title="Couldn't load effective parameters" />
      <p className="text-sm text-foreground">
        {preset.isLoading ? "Loading workflow…" : preset.isError ? "Workflow unavailable" : preset.data?.preset_name || "No workflow assigned"}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        Preset provenance {preset.data ? provenanceText(preset.data.provenance) : "not recorded"}
      </p>
      <div className="mt-3">
        {pickerOpen ? (
          <Select
            aria-label="Assign preset"
            allowClear
            placeholder="Assign preset"
            value={currentId}
            disabled={!assignAccess.allowed || assign.isPending || unassign.isPending}
            title={assignAccess.reason ?? undefined}
            options={presetOptions}
            onChange={(presetId) => {
              if (!assignAccess.allowed) return;
              if (presetId) assign.mutate(presetId);
              else unassign.mutate();
            }}
          />
        ) : (
          <Button
            type="button"
            size="sm"
            disabled={!assignAccess.allowed}
            title={assignAccess.reason ?? undefined}
            onClick={() => {
              if (!assignAccess.allowed) return;
              setPickerOpen(true);
            }}
          >
            Choose a workflow
          </Button>
        )}
      </div>
      <details className="td-maintenance-parameters">
      <summary>Parameters <span>{entries.length ? `· ${entries.length} recorded` : ""}</span></summary>
      {params.isLoading ? <p className="text-sm text-muted-foreground">Loading parameters</p> : null}
      {entries.length === 0 && params.isSuccess ? (
        <p className="text-sm text-muted-foreground">No parameters recorded.</p>
      ) : (
        <ul className="space-y-2">
          {entries.map(([key, value]) => (
            <li key={key} className="td-maintenance-parameter">
              <span className="text-sm font-medium text-foreground">{key}</span>
              <span className="text-sm text-foreground">{formatParamValue(value)}</span>
              <span className="text-sm text-muted-foreground">Source not recorded</span>
            </li>
          ))}
        </ul>
      )}
      </details>
      <details className="td-maintenance-parameters">
      <summary>Parameter overrides <span>Admin only</span></summary>
      <p className="td-maintenance-intro">Overrides replace workflow parameters for this table. Review the resulting plan before execution.</p>
      <div className="td-maintenance-override">
        <Input
          aria-label="Override name"
          placeholder="Param name"
          value={overrideKey}
          disabled={!canOverride || patch.isPending}
          onChange={(event) => setOverrideKey(event.target.value)}
          className="w-[200px]"
        />
        <Input
          aria-label="Override value"
          placeholder="Value"
          value={overrideValue}
          disabled={!canOverride || patch.isPending}
          onChange={(event) => setOverrideValue(event.target.value)}
          className="w-[140px]"
        />
        <Button
          type="button"
          size="sm"
          disabled={!canOverride || patch.isPending}
          title={overrideReason ?? undefined}
          onClick={addOverride}
        >
          Add override
        </Button>
      </div>
      {!canOverride ? <p className="mt-2 text-xs text-muted-foreground">{overrideReason}</p> : null}
      </details>
    </section>
  );
};
