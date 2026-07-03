import { Button, Select, Space, Typography } from "antd";
import { useState } from "react";
import type { DestinationRead, Mode, PresetRead, UUID } from "../api/types";
import type { Predicate } from "../lib/predicate";

export type BulkSelection =
  | { mode: "ids"; ids: UUID[]; totalSelected: number }
  | { mode: "filter"; filter: Predicate | null; totalSelected: number };

interface Props {
  selection: BulkSelection | null;
  presets: PresetRead[];
  destinations: DestinationRead[];
  assigning?: boolean;
  running?: boolean;
  onClear: () => void;
  onSelectAllMatching: () => void;
  onAssign: (presetId: UUID) => void;
  onPreview: () => void;
  onRun: (args: { destinationId: UUID; presetId?: UUID; mode: Mode }) => void;
}

export const BulkAssignBar = ({
  selection,
  presets,
  destinations,
  assigning,
  running,
  onClear,
  onSelectAllMatching,
  onAssign,
  onPreview,
  onRun
}: Props) => {
  const [destinationId, setDestinationId] = useState<UUID | undefined>();
  const [presetId, setPresetId] = useState<UUID | undefined>();
  if (!selection || selection.totalSelected === 0) return null;

  const activePresetId = presetId ?? presets[0]?.id;
  const activePreset = presets.find((preset) => preset.id === activePresetId);
  const activeDestinationId = destinationId ?? activePreset?.destination_id ?? destinations[0]?.id;

  return (
    <div className="bulk-bar" data-testid="bulk-assign-bar">
      <Space wrap size={8}>
        <Typography.Text strong>
          {selection.mode === "filter"
            ? `${selection.totalSelected.toLocaleString()} matching tables`
            : `${selection.totalSelected.toLocaleString()} selected`}
        </Typography.Text>
        {selection.mode === "ids" ? (
          <Button type="link" onClick={onSelectAllMatching}>
            Select all matching filter
          </Button>
        ) : null}
        <Select
          placeholder="Assign preset"
          value={activePresetId}
          options={presets.map((preset) => ({ label: preset.name, value: preset.id }))}
          style={{ width: 210 }}
          onChange={(next) => {
            setPresetId(next);
            onAssign(next);
          }}
          loading={assigning}
        />
        <Button onClick={onPreview}>Preview</Button>
        <Select
          placeholder="Run engine"
          value={activeDestinationId}
          options={destinations.map((destination) => ({ label: destination.name, value: destination.id }))}
          style={{ width: 190 }}
          onChange={setDestinationId}
          loading={running}
        />
        <Button
          type="primary"
          loading={running}
          disabled={!activeDestinationId}
          onClick={() =>
            activeDestinationId
              ? onRun({
                  destinationId: activeDestinationId,
                  presetId: activePresetId,
                  mode: activePreset?.mode ?? "standard"
                })
              : undefined
          }
        >
          Run now
        </Button>
        <Button onClick={onClear}>Clear</Button>
      </Space>
    </div>
  );
};
