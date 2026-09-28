import { LayoutDashboard, Settings, Zap } from "lucide-react";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";
import { ConfirmAction } from "../confirm";
import { Button } from "../ui/button";
import { Select } from "../ui/select";
import { Tooltip, TooltipContent, TooltipTrigger } from "../ui/tooltip";
import { formatEngineLabel } from "../../lib/engines";
import { useWriteControl } from "../../lib/useWriteControl";
import { provenanceLabel } from "./helpers";

interface EngineActionStripProps {
  onOpenSettings: () => void;
}

/**
 * Replaces the old permanent 33%-width sidebar's "Preset & Engine" card. This is genuinely
 * every-tab-relevant (assigning a preset and running maintenance aren't scoped to Overview),
 * so it stays visible on every tab -- but as a compact header row, not a column that eats a
 * third of the screen on Lineage/Activity where none of this is what's being looked at.
 */
export const EngineActionStrip = ({ onOpenSettings }: EngineActionStripProps) => {
  const {
    row,
    effectivePreset,
    effectiveDestination,
    assignPresetOptions,
    assign,
    unassign,
    presets,
    compatiblePresets,
    queuedMaintenanceRunId,
    run,
    handleRunAction,
    setPreviewOpen,
    manifestRewrite
  } = useTableDetailContext();
  const runAccess = useWriteControl("runMaintenance");
  const manifestAccess = useWriteControl("manifestRewrite");
  const assignAccess = useWriteControl("assignPreset");

  const presetLoading = assign.isPending || presets.isLoading || compatiblePresets.isLoading || unassign.isPending;

  return (
    <div className="td-engine-strip flex flex-wrap items-center gap-2 border-b border-border px-4 py-2.5">
      <span className="text-xs font-medium text-muted-foreground">Preset</span>
      <Select
        aria-label="Assign preset"
        className="h-8 w-[220px] text-xs"
        allowClear
        placeholder="Assign preset"
        value={effectivePreset.data?.preset_id ?? undefined}
        disabled={!row || presetLoading || !assignAccess.allowed}
        title={assignAccess.reason ?? undefined}
        onChange={(presetId) => {
          if (presetId) assign.mutate(presetId);
          else unassign.mutate();
        }}
        options={assignPresetOptions}
      />
      {effectivePreset.data?.provenance ? (
        <span className="text-[11px] text-muted-foreground">
          Active: <strong>{effectivePreset.data?.preset_name}</strong> (
          {provenanceLabel(effectivePreset.data?.provenance)})
          {effectiveDestination ? (
            <> · {effectiveDestination.name} ({formatEngineLabel(effectiveDestination.engine)})</>
          ) : null}
        </span>
      ) : null}

      <div className="ml-auto flex items-center gap-1.5">
        {queuedMaintenanceRunId || run.isPending ? (
          <Button
            variant="primary"
            size="sm"
            loading={run.isPending}
            onClick={handleRunAction}
            disabled={!row || run.isPending || !queuedMaintenanceRunId}
            aria-label={run.isPending ? "Queueing run" : "Open maintenance run"}
          >
            <Zap className="size-3.5" />
            {run.isPending ? "Queueing run…" : "Open maintenance run"}
          </Button>
        ) : (
          <ConfirmAction
            title="Run maintenance?"
            description="Queues a real maintenance job using the assigned preset and destination."
            onConfirm={() => run.mutate()}
            disabled={!runAccess.allowed || !row || !effectivePreset.data?.preset_id}
          >
            <Button
              variant="primary"
              size="sm"
              disabled={!runAccess.allowed || !row || !effectivePreset.data?.preset_id}
              title={runAccess.reason ?? undefined}
              aria-label="Run maintenance"
            >
              <Zap className="size-3.5" />
              Run maintenance
            </Button>
          </ConfirmAction>
        )}
        <Button variant="secondary" size="sm" onClick={() => setPreviewOpen(true)} disabled={!runAccess.allowed || !row || !effectivePreset.data?.preset_id} title={runAccess.reason ?? undefined}>
          <LayoutDashboard className="size-3.5" />
          Preview
        </Button>
        <ConfirmAction
          title="Rewrite manifests?"
          description="Queues manifest-only maintenance without data file compaction."
          onConfirm={() => manifestRewrite.mutate()}
          disabled={!manifestAccess.allowed || !row}
        >
          <Button
            variant="secondary"
            size="sm"
            loading={manifestRewrite.isPending}
            disabled={!manifestAccess.allowed || !row}
            title={manifestAccess.reason ?? undefined}
          >
            Manifest
          </Button>
        </ConfirmAction>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon" aria-label="Table settings" onClick={onOpenSettings}>
              <Settings className="size-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Compliance, ownership &amp; alert subscribers</TooltipContent>
        </Tooltip>
      </div>
    </div>
  );
};
