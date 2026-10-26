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
import { Badge } from "../../ui/badge";
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
  switch (value) {
    case "assignment":
      return "Assigned to this table by an operator.";
    case "rule":
      return "Picked by a matching assignment rule.";
    case "rule_after_stale_pin":
      return "Picked by a rule because the pinned preset no longer applies.";
    case "default":
      return "The platform default. Nothing is assigned to this table directly.";
    case "none":
      return "No preset assigned.";
    default:
      return "How this preset was chosen wasn't recorded.";
  }
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

  const presetParams = params.data?.preset_params ?? {};
  const overrides = params.data?.param_overrides ?? {};
  const entries = [...new Set([...Object.keys(presetParams), ...Object.keys(overrides)])]
    .sort()
    .map((key) => {
      const overridden = key in overrides;
      return {
        key,
        value: overridden ? overrides[key] : presetParams[key],
        overridden,
        presetValue: key in presetParams ? presetParams[key] : undefined
      };
    });
  const overrideCount = entries.filter((entry) => entry.overridden).length;
  const currentId = preset.data?.preset_id ?? undefined;
  const currentPaused = Boolean(currentId && presets.data?.find((item) => item.id === currentId)?.paused);
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

  const workflowName = preset.isLoading
    ? "Loading workflow…"
    : preset.isError
      ? "Workflow unavailable"
      : preset.data?.preset_name || "No workflow assigned";

  return (
    <div id="maintenance-settings" className="td-maintenance-settings">
      <QueryErrorAlert error={preset.error} title="Couldn't load workflow" />
      <QueryErrorAlert error={params.error} title="Couldn't load effective parameters" />
      <div className="td-maintenance-workflow">
        <div className="td-maintenance-workflow-text">
          <p className="td-maintenance-kicker">Workflow</p>
          <p className="td-maintenance-workflow-name">{workflowName}</p>
          <p className="td-maintenance-workflow-why">{preset.data ? provenanceText(preset.data.provenance) : null}</p>
          {currentPaused ? (
            <p className="td-maintenance-workflow-why" role="status">
              Paused, this table is waiting. Nothing runs until the preset is resumed.
            </p>
          ) : null}
        </div>
        <dl className="td-maintenance-workflow-stats">
          <div>
            <dt>Parameters</dt>
            <dd>{params.isSuccess ? entries.length : "—"}</dd>
          </div>
          <div>
            <dt>Overridden here</dt>
            <dd data-active={overrideCount > 0 || undefined}>{params.isSuccess ? overrideCount : "—"}</dd>
          </div>
        </dl>
        <div className="td-maintenance-workflow-action">
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
              variant="secondary"
              disabled={!assignAccess.allowed}
              title={assignAccess.reason ?? undefined}
              onClick={() => {
                if (!assignAccess.allowed) return;
                setPickerOpen(true);
              }}
            >
              {currentId ? "Change workflow" : "Choose a workflow"}
            </Button>
          )}
        </div>
      </div>

      <div className="td-maintenance-params">
        <h3 className="td-maintenance-subhead">Effective parameters</h3>
        {params.isLoading ? <p className="td-maintenance-intro">Loading parameters</p> : null}
        {entries.length === 0 && params.isSuccess ? (
          <p className="td-maintenance-intro">This workflow sets no parameters, so engine defaults apply.</p>
        ) : null}
        {entries.length > 0 ? (
          <table className="td-maintenance-param-table">
            <thead>
              <tr>
                <th scope="col">Parameter</th>
                <th scope="col">Value</th>
                <th scope="col">Comes from</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.key} data-overridden={entry.overridden || undefined}>
                  <td>
                    <code>{entry.key}</code>
                  </td>
                  <td>{formatParamValue(entry.value)}</td>
                  <td>
                    {entry.overridden ? (
                      <>
                        <Badge variant="warning">Override</Badge>
                        {entry.presetValue !== undefined ? (
                          <span className="td-maintenance-param-was">preset: {formatParamValue(entry.presetValue)}</span>
                        ) : null}
                      </>
                    ) : (
                      <Badge variant="secondary">Preset</Badge>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </div>

      <details className="td-maintenance-parameters">
        <summary>
          Override a parameter <span>Admin only</span>
        </summary>
        <p className="td-maintenance-intro">
          An override pins one value for this table and survives preset reassignment. Review the resulting plan before
          running maintenance.
        </p>
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
          <Button type="button" size="sm" disabled={!canOverride || patch.isPending} title={overrideReason ?? undefined} onClick={addOverride}>
            Add override
          </Button>
        </div>
        {!canOverride ? <p className="mt-2 text-xs text-muted-foreground">{overrideReason}</p> : null}
      </details>
    </div>
  );
};
