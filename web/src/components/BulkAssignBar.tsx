import { Button, Select, Space, Tag, Tooltip, Typography } from "antd";
import { useState } from "react";
import type { DestinationRead, Mode, PresetRead, UUID } from "../api/types";
import type { Predicate } from "../lib/predicate";
import { ConfirmAction } from "./confirm";

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
  onRun: (args: { destinationId: UUID; presetId?: UUID; mode: Mode }) => void;
  onManifestRewrite?: (args: { destinationId: UUID; presetId?: UUID }) => void;
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

  const activePresetId = presetId ?? presets[0]?.id;
  const activePreset = presets.find((preset) => preset.id === activePresetId);
  const activeDestinationId = destinationId ?? activePreset?.destination_id ?? destinations[0]?.id;
  const activeDestination = destinations.find((destination) => destination.id === activeDestinationId);
  const isFilterSelection = selection.mode === "filter";
  const presetOptions = presets.map((preset) => ({
    label: [preset.name, preset.description ?? `${formatMode(preset.mode)} maintenance preset`]
      .filter(Boolean)
      .join(" · "),
    value: preset.id
  }));
  const destinationOptions = destinations.map((destination) => ({
    label: `${destination.name} · ${formatEngine(destination.engine)}`,
    value: destination.id
  }));

  return (
    <div className="bulk-bar" data-testid="bulk-assign-bar">
      <Space orientation="vertical" size={6}>
        <Space wrap size={8}>
          {isFilterSelection ? <Tag color="processing">Filter selection</Tag> : null}
          <Typography.Text strong>
            {isFilterSelection
              ? `All ${selection.totalSelected.toLocaleString()} matching tables selected`
              : `${selection.totalSelected.toLocaleString()} selected`}
          </Typography.Text>
          <Tooltip
            title={
              activePreset?.description ??
              "Choose the maintenance preset to assign to the selected tables."
            }
          >
            <Select
              aria-label="Assign preset"
              placeholder="Assign preset"
              value={activePresetId}
              options={presetOptions}
              optionFilterProp="label"
              style={{ width: 250 }}
              onChange={(next) => {
                setPresetId(next);
                onAssign(next);
              }}
              loading={assigning}
            />
          </Tooltip>
          <Tooltip title="Preview the planned maintenance steps before queueing a run.">
            <Button onClick={onPreview}>Preview</Button>
          </Tooltip>
          {onScan ? (
            <ConfirmAction
              title="Scan selected tables?"
              description="Queue a metadata scan for every table in the current selection."
              onConfirm={onScan}
            >
              <Tooltip title="Queue a metadata scan for the current selection.">
                <Button loading={scanning}>Scan selected</Button>
              </Tooltip>
            </ConfirmAction>
          ) : null}
          <Tooltip
            title={
              activeDestination
                ? `${activeDestination.name} runs maintenance through ${formatEngine(activeDestination.engine)}.`
                : "Choose the engine that will run maintenance."
            }
          >
            <Select
              aria-label="Run engine"
              placeholder="Run engine"
              value={activeDestinationId}
              options={destinationOptions}
              optionFilterProp="label"
              style={{ width: 220 }}
              onChange={setDestinationId}
              loading={running}
            />
          </Tooltip>
          <ConfirmAction
            title={
              isFilterSelection
                ? `Run maintenance on all ${selection.totalSelected.toLocaleString()} matching tables?`
                : `Run maintenance on ${selection.totalSelected.toLocaleString()} selected tables?`
            }
            description="This queues real maintenance jobs. Review the preset and engine before confirming."
            onConfirm={() =>
              activeDestinationId
                ? onRun({
                    destinationId: activeDestinationId,
                    presetId: activePresetId,
                    mode: activePreset?.mode ?? "standard"
                  })
                : undefined
            }
          >
            <Tooltip title="Queue maintenance for the current selection using the chosen preset and engine.">
              <span>
                <Button type="primary" loading={running} disabled={!activeDestinationId}>
                  Run now
                </Button>
              </span>
            </Tooltip>
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
                activeDestinationId
                  ? onManifestRewrite({
                      destinationId: activeDestinationId,
                      presetId: activePresetId
                    })
                  : undefined
              }
            >
              <Tooltip title="Queue manifest rewrite only — no data file compaction.">
                <span>
                  <Button loading={running} disabled={!activeDestinationId}>
                    Manifest rewrite
                  </Button>
                </span>
              </Tooltip>
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
              <Tooltip title="Remove preset assignments from the current selection.">
                <Button loading={assigning}>Unassign</Button>
              </Tooltip>
            </ConfirmAction>
          ) : null}
          <Tooltip title="Clear the current bulk selection.">
            <Button onClick={onClear}>Clear</Button>
          </Tooltip>
        </Space>
        {isFilterSelection ? (
          <Typography.Text type="secondary" className="bulk-selection-help">
            Actions now apply to every table matching the current filters, across all pages.
          </Typography.Text>
        ) : null}
      </Space>
    </div>
  );
};

const formatMode = (mode: Mode): string => mode.replace(/_/g, " ");

const formatEngine = (engine: DestinationRead["engine"]): string => engine.replace(/_/g, " ");
