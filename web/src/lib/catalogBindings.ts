import type { DestinationRead, JsonObject, PresetRead, SourceRead, UUID } from "../api/types";

export type CatalogBindingFormValue = {
  source_id: UUID;
  trino_catalog?: string;
};

/** Display catalog name for a Cryo lakehouse (matches backend catalog_name_for_source). */
export const sourceCatalogName = (source: SourceRead): string => {
  const cfg = source.config ?? {};
  const name = cfg.catalog_name ?? cfg.catalog;
  if (typeof name === "string" && name.trim()) return name.trim();
  return source.name;
};

export const parseCatalogBindings = (config: JsonObject | undefined | null): CatalogBindingFormValue[] => {
  const raw = config?.catalog_bindings;
  if (!Array.isArray(raw)) return [];
  const out: CatalogBindingFormValue[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const record = item as Record<string, unknown>;
    const sourceId = record.source_id;
    if (typeof sourceId !== "string" || !sourceId || seen.has(sourceId)) continue;
    seen.add(sourceId);
    const override = record.trino_catalog;
    out.push({
      source_id: sourceId,
      trino_catalog: typeof override === "string" && override.trim() ? override.trim() : undefined
    });
  }
  return out;
};

/** Whether a destination may maintain tables from ``sourceId``. Non-Trino always true. */
export const destinationServesSource = (
  destination: DestinationRead | null | undefined,
  sourceId: UUID | null | undefined
): boolean => {
  if (!destination) return false;
  if (destination.engine !== "trino") return true;
  if (!sourceId) return false;
  return parseCatalogBindings(destination.config).some((b) => b.source_id === sourceId);
};

/** Filter presets to those compatible with every given source id (intersection). */
export const filterPresetsForSources = (
  presets: PresetRead[],
  destinations: DestinationRead[],
  sourceIds: Array<UUID | null | undefined>
): PresetRead[] => {
  const destById = new Map(destinations.map((d) => [d.id, d]));
  const ids = sourceIds.filter((id): id is UUID => typeof id === "string" && Boolean(id));
  if (ids.length === 0) {
    // No tables / unknown sources: hide Trino presets (empty bindings serve none).
    return presets.filter((preset) => {
      const dest = destById.get(preset.destination_id);
      return dest?.engine !== "trino";
    });
  }
  return presets.filter((preset) => {
    const dest = destById.get(preset.destination_id);
    if (!dest) return false;
    return ids.every((sourceId) => destinationServesSource(dest, sourceId));
  });
};

export const filterPresetsForSource = (
  presets: PresetRead[],
  destinations: DestinationRead[],
  sourceId: UUID | null | undefined
): PresetRead[] => filterPresetsForSources(presets, destinations, [sourceId]);
