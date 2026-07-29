import type { PresetRead } from "../../../api/types";
import { PresetConfigForm } from "../../../components/presets/PresetConfigForm";

export const PresetConfigTab = ({
  preset,
  loading,
  onSaved
}: {
  preset?: PresetRead;
  loading?: boolean;
  onSaved: () => void;
}) => <PresetConfigForm preset={preset} loading={loading} onSaved={onSaved} />;
