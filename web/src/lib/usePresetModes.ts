import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { getPresetModes } from "../api/client";
import type { PresetRead } from "../api/types";
import { useAuth } from "./auth";
import { modeOperations } from "./presetModes";

/** GET /presets/modes: what each mode runs. Static per server build, so fetched once. */
export const usePresetModes = () => {
  const { api } = useAuth();
  return useQuery({ queryKey: ["preset-modes"], queryFn: () => getPresetModes(api), staleTime: Infinity });
};

export interface PresetOperations {
  operations: string[];
  loading: boolean;
}

/**
 * What a preset runs: its mode's operations, the same list the Config tab shows. The stored
 * `operations` is used only when the catalog failed to load or doesn't know the mode; while
 * the catalog loads the list is empty rather than a possibly stale stored one.
 */
export const usePresetOperations = (
  preset: Pick<PresetRead, "mode" | "operations"> | undefined
): PresetOperations => {
  const modes = usePresetModes();
  return useMemo(() => {
    if (!preset) return { operations: [], loading: true };
    if (modes.data) return { operations: modeOperations(modes.data, preset.mode) ?? preset.operations ?? [], loading: false };
    if (modes.isError) return { operations: preset.operations ?? [], loading: false };
    return { operations: [], loading: true };
  }, [modes.data, modes.isError, preset]);
};
