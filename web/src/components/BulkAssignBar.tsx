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
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Select } from "./ui/select";

export type BulkSelection =
  | { mode: "ids"; ids: UUID[]; totalSelected: number }
  | { mode: "filter"; filter: Predicate | null; totalSelected: number };

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
}

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
  onScan
}: Props) => {
  const [destinationId, setDestinationId] = useState<UUID | undefined>();
  const [presetId, setPresetId] = useState<UUID | undefined>();
  if (!selection || selection.totalSelected === 0) return null;

  const activePresetId = presetId;
  const activePreset = presets.find((preset) => preset.id === activePresetId);
  const activeDestinationId = destinationId ?? activePreset?.destination_id;
  const activeDestination = destinations.find((destination) => destination.id === activeDestinationId);
  const isFilterSelection = selection.mode === "filter";
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
  const runDisabled = !activePresetId || !activeDestinationId;
  const manifestRewriteDisabled = runDisabled || isNativeDestination(activeDestination);
  const presetTooltip =
    activePreset?.description ??
    "Choose the maintenance preset to assign to the selected tables. Run is disabled until a preset is selected.";
  const engineTooltip = activeDestination
    ? `${activeDestination.name} runs maintenance through ${formatEngineLabel(activeDestination.engine, activeDestination.config)}.`
    : "Choose the engine that will run maintenance.";

  return (
    <div className="bulk-bar" data-testid="bulk-assign-bar">
      <div className="flex flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-2">
          {isFilterSelection ? <Badge variant="default">Filter selection</Badge> : null}
          <span className="font-semibold">
            {isFilterSelection
              ? `All ${selection.totalSelected.toLocaleString()} matching tables selected`
              : `${selection.totalSelected.toLocaleString()} selected`}
          </span>
          <Select
            aria-label="Assign preset"
            title={presetTooltip}
            placeholder="Select preset"
            value={activePresetId}
            options={presetOptions}
            allowClear
            loading={assigning}
            onChange={(next) => {
              setPresetId(next);
              if (next) onAssign(next);
            }}
            style={{ width: 250 }}
          />
          <Button title="Preview the planned maintenance steps before queueing a run." onClick={onPreview}>
            Preview
          </Button>
          {onScan ? (
            <ConfirmAction
              title="Scan selected tables?"
              description="Queue a metadata scan for every table in the current selection."
              onConfirm={onScan}
            >
              <Button title="Queue a metadata scan for the current selection." loading={scanning}>
                Scan selected
              </Button>
            </ConfirmAction>
          ) : null}
          <Select
            aria-label="Run engine"
            title={engineTooltip}
            placeholder="Run engine"
            value={activeDestinationId}
            options={destinationOptions}
            loading={running}
            onChange={setDestinationId}
            style={{ width: 220 }}
          />
          <ConfirmAction
            title={
              isFilterSelection
                ? `Run maintenance on all ${selection.totalSelected.toLocaleString()} matching tables?`
                : `Run maintenance on ${selection.totalSelected.toLocaleString()} selected tables?`
            }
            description="This queues real maintenance jobs. Review the preset and engine before confirming."
            onConfirm={() =>
              activePresetId && activeDestinationId
                ? onRun({
                    destinationId: activeDestinationId,
                    presetId: activePresetId,
                    mode: activePreset?.mode ?? "standard"
                  })
                : undefined
            }
          >
            <span title="Queue maintenance for the current selection using the chosen preset and engine.">
              <Button variant="primary" loading={running} disabled={runDisabled}>
                Run now
              </Button>
            </span>
          </ConfirmAction>
          {onManifestRewrite ? (
            <ConfirmAction
              title={
                isFilterSelection
                  ? `Rewrite manifests for all ${selection.totalSelected.toLocaleString()} matching tables?`
                  : `Rewrite manifests for ${selection.totalSelected.toLocaleString()} selected tables?`
              }
              description="Runs manifest-only maintenance (optimize_manifests) without data file compaction."
              onConfirm={() =>
                activePresetId && activeDestinationId
                  ? onManifestRewrite({
                      destinationId: activeDestinationId,
                      presetId: activePresetId
                    })
                  : undefined
              }
            >
              <span
                title={
                  manifestRewriteDisabled && isNativeDestination(activeDestination)
                    ? nativeUnsupportedOpTooltip
                    : "Queue manifest rewrite only — no data file compaction."
                }
              >
                <Button loading={running} disabled={manifestRewriteDisabled}>
                  Manifest rewrite
                </Button>
              </span>
            </ConfirmAction>
          ) : null}
          {onUnassign ? (
            <ConfirmAction
              title={
                isFilterSelection
                  ? `Unassign presets from all ${selection.totalSelected.toLocaleString()} matching tables?`
                  : `Unassign presets from ${selection.totalSelected.toLocaleString()} selected tables?`
              }
              description="Clears manual preset assignments for the selected tables."
              onConfirm={onUnassign}
            >
              <Button title="Remove preset assignments from the current selection." loading={assigning}>
                Unassign
              </Button>
            </ConfirmAction>
          ) : null}
          <Button title="Clear the current bulk selection." onClick={onClear}>
            Clear
          </Button>
        </div>
        {isFilterSelection ? (
          <p className="bulk-selection-help text-sm text-muted-foreground">
            Actions now apply to every table matching the current filters, across all pages.
          </p>
        ) : null}
      </div>
    </div>
  );
};

const formatMode = (mode: Mode): string => mode.replace(/_/g, " ");
