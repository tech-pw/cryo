import { describe, expect, it } from "vitest";
import type { DestinationRead, PresetRead, SourceRead } from "../api/types";
import {
  destinationServesSource,
  filterPresetsForSources,
  parseCatalogBindings,
  sourceCatalogName
} from "./catalogBindings";

const source = (id: string, name: string, catalogName?: string): SourceRead =>
  ({
    id,
    name,
    type: "rest",
    config: catalogName ? { catalog_name: catalogName } : {},
    enabled: true,
    maintenance_schema_names: [],
    created_at: "",
    updated_at: ""
  }) as SourceRead;

const destination = (
  id: string,
  engine: DestinationRead["engine"],
  bindings?: Array<{ source_id: string; trino_catalog?: string }>
): DestinationRead =>
  ({
    id,
    name: id,
    engine,
    config: bindings ? { catalog_bindings: bindings } : {},
    enabled: true,
    created_at: "",
    updated_at: ""
  }) as DestinationRead;

const preset = (id: string, destinationId: string): PresetRead =>
  ({
    id,
    name: id,
    destination_id: destinationId,
    mode: "standard",
    operations: [],
    params: {},
    priority: 100,
    enabled: true,
    guardrails: {},
    is_default: false,
    created_at: "",
    updated_at: ""
  }) as PresetRead;

describe("catalogBindings", () => {
  it("parses bindings and source catalog names", () => {
    expect(sourceCatalogName(source("s1", "lake", "rest"))).toBe("rest");
    expect(sourceCatalogName(source("s1", "lake"))).toBe("lake");
    expect(
      parseCatalogBindings({
        catalog_bindings: [
          { source_id: "s1", trino_catalog: "iceberg" },
          { source_id: "s1" },
          { source_id: "s2" }
        ]
      })
    ).toEqual([
      { source_id: "s1", trino_catalog: "iceberg" },
      { source_id: "s2", trino_catalog: undefined }
    ]);
  });

  it("filters presets by Trino catalog bindings (intersection)", () => {
    const trino = destination("trino", "trino", [{ source_id: "s1" }]);
    const native = destination("native", "native");
    const presets = [preset("p-trino", "trino"), preset("p-native", "native")];
    const dests = [trino, native];

    expect(destinationServesSource(trino, "s1")).toBe(true);
    expect(destinationServesSource(trino, "s2")).toBe(false);
    expect(destinationServesSource(native, "s2")).toBe(true);

    expect(filterPresetsForSources(presets, dests, ["s1"]).map((p) => p.id)).toEqual([
      "p-trino",
      "p-native"
    ]);
    expect(filterPresetsForSources(presets, dests, ["s2"]).map((p) => p.id)).toEqual(["p-native"]);
    expect(filterPresetsForSources(presets, dests, ["s1", "s2"]).map((p) => p.id)).toEqual([
      "p-native"
    ]);
  });
});
