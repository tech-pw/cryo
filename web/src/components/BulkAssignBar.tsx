import { MoreHorizontal, Play, X } from "lucide-react";
import { useState } from "react";
import type { DestinationRead, Mode, PresetRead, UUID } from "../api/types";
import type { Predicate } from "../lib/predicate";
import {
  formatDestinationLabel,
  formatEngineLabel,
  isNativeDestination,
  nativeUnsupportedOpTooltip
} from "../lib/engines";
import { ConfirmAction } from "./confirm";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle
} from "./ui/alert-dialog";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from "./ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "./ui/dropdown-menu";
import { Label } from "./ui/label";
import { Segmented } from "./ui/segmented";
import { Select } from "./ui/select";

export type BulkSelection =
  | { mode: "ids"; ids: UUID[]; totalSelected: number }
  | { mode: "filter"; filter: Predicate | null; totalSelected: number };

type RunScope = "workflow" | "manifests";

interface Props {
  selection: BulkSelection | null;
  presets: PresetRead[];
  destinations: DestinationRead[];
  assigning?: boolean;
  running?: boolean;
  scanning?: boolean;
  onClear: () => void;
  onAssign: (presetId: UUID) => void;
  onPreview: () => void;
  onRun: (args: { destinationId: UUID; presetId: UUID; mode: Mode }) => void;
  onManifestRewrite?: (args: { destinationId: UUID; presetId: UUID }) => void;
  onUnassign?: () => void;
  onScan?: () => void;
  /** Total tables matching the current filters; offers "select all" when larger than the selection. */
  matchingTotal?: number;
  onSelectAllMatching?: () => void;
  /** Selected rows that are already running, queued, held or excluded, so a run would likely skip them. */
  likelySkipped?: number;
}

const formatMode = (mode: Mode): string => mode.replace(/_/g, " ");

const runDisabledReason = (
  preset: PresetRead | undefined,
  destination: DestinationRead | undefined,
  scope: RunScope
): string | null => {
  if (!preset) return "Choose the workflow to run.";
  if (!destination) return "Choose the engine that runs it.";
  if (scope === "manifests" && isNativeDestination(destination)) return nativeUnsupportedOpTooltip;
  return null;
};

export const BulkAssignBar = ({
  selection,
  presets,
  destinations,
  assigning,
  running,
  scanning,
  onClear,
  onAssign,
  onPreview,
  onRun,
  onManifestRewrite,
  onUnassign,
  onScan,
  matchingTotal,
  onSelectAllMatching,
  likelySkipped = 0
}: Props) => {
  const [runOpen, setRunOpen] = useState(false);
  const [unassignOpen, setUnassignOpen] = useState(false);
  const [runPresetId, setRunPresetId] = useState<UUID | undefined>();
  const [destinationId, setDestinationId] = useState<UUID | undefined>();
  const [scope, setScope] = useState<RunScope>("workflow");
  if (!selection || selection.totalSelected === 0) return null;

  const total = selection.totalSelected;
  const isFilterSelection = selection.mode === "filter";
  const scopeText = isFilterSelection
    ? `all ${total.toLocaleString()} matching tables`
    : `${total.toLocaleString()} selected ${total === 1 ? "table" : "tables"}`;
  const runPreset = presets.find((preset) => preset.id === runPresetId);
  const activeDestinationId = destinationId ?? runPreset?.destination_id;
  const activeDestination = destinations.find((destination) => destination.id === activeDestinationId);
  const disabledReason = runDisabledReason(runPreset, activeDestination, scope);
  const canSelectAll =
    !isFilterSelection && onSelectAllMatching && matchingTotal != null && matchingTotal > total;
  const presetOptions = presets.map((preset) => ({
    label: [preset.name, preset.description ?? `${formatMode(preset.mode)} maintenance preset`]
      .filter(Boolean)
      .join(" · "),
    value: preset.id
  }));
  const destinationOptions = destinations.map((destination) => ({
    label: formatDestinationLabel(destination),
    value: destination.id
  }));

  const queueRun = () => {
    if (!runPreset || !activeDestinationId || disabledReason) return;
    if (scope === "manifests" && onManifestRewrite) {
      onManifestRewrite({ destinationId: activeDestinationId, presetId: runPreset.id });
    } else {
      onRun({ destinationId: activeDestinationId, presetId: runPreset.id, mode: runPreset.mode ?? "standard" });
    }
    setRunOpen(false);
  };

  return (
    <div className="bulk-bar" data-testid="bulk-assign-bar" role="region" aria-label="Selection actions">
      <div className="bulk-bar__count">
        {isFilterSelection ? <Badge variant="default">Filter selection</Badge> : null}
        <span className="bulk-bar__count-value">
          {isFilterSelection
            ? `All ${total.toLocaleString()} matching tables selected`
            : `${total.toLocaleString()} selected`}
        </span>
        {canSelectAll ? (
          <button
            type="button"
            className="cursor-pointer text-xs font-medium text-[var(--color-link)] hover:underline"
            onClick={onSelectAllMatching}
          >
            Select all {matchingTotal.toLocaleString()} matching
          </button>
        ) : null}
      </div>
      <div className="bulk-bar__group" role="group" aria-label="Assign">
        <Select
          aria-label="Assign preset"
          title="Pin a workflow to the selected tables. Applies as soon as you pick one."
          placeholder="Assign workflow…"
          value=""
          options={presetOptions}
          loading={assigning}
          onChange={(next) => {
            if (!next) return;
            setRunPresetId(next);
            onAssign(next);
          }}
          style={{ width: 172 }}
        />
        <Button size="sm" variant="ghost" title="Preview the planned maintenance steps." onClick={onPreview}>
          Preview plan
        </Button>
      </div>
      <div className="bulk-bar__group" role="group" aria-label="Run">
        {onScan ? (
          <ConfirmAction
            title={`Scan ${scopeText}?`}
            description="Queues a full metadata scan. Health, savings and the next nightly plan use the fresh stats."
            onConfirm={onScan}
          >
            <Button size="sm" title="Queue a metadata scan for the current selection." loading={scanning}>
              Scan
            </Button>
          </ConfirmAction>
        ) : null}
        <Button size="sm" variant="primary" className="gap-1.5" loading={running} onClick={() => setRunOpen(true)}>
          <Play className="size-3.5" aria-hidden="true" />
          Run maintenance…
        </Button>
      </div>
      <div className="bulk-bar__group bulk-bar__group--end">
        {onUnassign ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="icon" variant="ghost" aria-label="More selection actions" className="size-8">
                <MoreHorizontal className="size-4" aria-hidden="true" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem
                className="text-[var(--color-status-danger)] focus:text-[var(--color-status-danger)]"
                onSelect={() => setUnassignOpen(true)}
              >
                Remove workflow pins…
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
        <Button size="sm" variant="ghost" className="gap-1" title="Clear the current selection." onClick={onClear}>
          <X className="size-3.5" aria-hidden="true" />
          Clear
        </Button>
      </div>
      {isFilterSelection ? (
        <p className="bulk-selection-help">Actions apply to every table matching the current filters, across all pages.</p>
      ) : null}

      <Dialog open={runOpen} onOpenChange={setRunOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Run maintenance on {scopeText}</DialogTitle>
            <DialogDescription>
              Queues real maintenance jobs now, outside the nightly schedule. The governor still paces them against
              engine load.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1">
              <Label htmlFor="bulk-run-workflow">Workflow</Label>
              <Select
                id="bulk-run-workflow"
                className="w-full min-w-0"
                aria-label="Run workflow"
                placeholder="Choose a workflow"
                value={runPresetId}
                options={presetOptions}
                onChange={(next) => setRunPresetId(next || undefined)}
              />
            </div>
            {onManifestRewrite ? (
              <div className="flex flex-col gap-1">
                <span className="text-sm font-medium">Operations</span>
                <Segmented
                  aria-label="Operations to run"
                  value={scope}
                  onChange={setScope}
                  options={[
                    { label: "Full workflow", value: "workflow" },
                    { label: "Manifests only", value: "manifests" }
                  ]}
                />
                <p className="text-xs text-muted-foreground">
                  {scope === "workflow"
                    ? "Every operation the workflow enables, in the standard order."
                    : "Rewrites manifests only. No data files are compacted."}
                </p>
              </div>
            ) : null}
            <div className="flex flex-col gap-1">
              <Label htmlFor="bulk-run-engine">Engine</Label>
              <Select
                id="bulk-run-engine"
                className="w-full min-w-0"
                aria-label="Run engine"
                placeholder="Choose an engine"
                value={activeDestinationId}
                options={destinationOptions}
                onChange={(next) => setDestinationId(next || undefined)}
              />
              {activeDestination ? (
                <p className="text-xs text-muted-foreground">
                  Runs through {formatEngineLabel(activeDestination.engine, activeDestination.config)}.
                </p>
              ) : null}
            </div>
            {likelySkipped > 0 ? (
              <p className="rounded-[var(--radius-default)] bg-[var(--color-surface-sunken)] px-2.5 py-2 text-xs text-muted-foreground">
                {likelySkipped === 1
                  ? "1 of the selected tables is already running, queued, held or excluded and will likely be skipped."
                  : `${likelySkipped.toLocaleString()} of the selected tables are already running, queued, held or excluded and will likely be skipped.`}
              </p>
            ) : null}
          </div>
          <DialogFooter className="items-center gap-2 sm:justify-between">
            <p className="text-xs text-muted-foreground" role="status">
              {disabledReason ?? ""}
            </p>
            <div className="flex gap-2">
              <Button variant="ghost" onClick={() => setRunOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" disabled={Boolean(disabledReason)} loading={running} onClick={queueRun}>
                Queue run
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={unassignOpen} onOpenChange={setUnassignOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove workflow pins from {scopeText}?</AlertDialogTitle>
            <AlertDialogDescription>
              Clears manual workflow assignments. Preset rules can still match these tables on the next run.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => onUnassign?.()}
            >
              Remove pins
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};
