import type { PresetRead } from "../../../api/types";
import { PresetConfigForm } from "../../../components/presets/PresetConfigForm";
import { PresetRulesSection } from "../../../components/presets/PresetRulesSection";

export const PresetConfigTab = ({
  preset,
  loading,
  onSaved,
  onDirtyChange
}: {
  preset?: PresetRead;
  loading?: boolean;
  onSaved: () => void;
  onDirtyChange?: (dirty: boolean) => void;
}) => (
  <div className="flex flex-col gap-6">
    <PresetConfigForm preset={preset} loading={loading} onSaved={onSaved} onDirtyChange={onDirtyChange} />
    {preset ? <PresetRulesSection presetId={preset.id} /> : null}
  </div>
);
