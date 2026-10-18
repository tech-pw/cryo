import { describe, expect, it } from "vitest";
import type { TableStatsRead } from "../../../api/types";
import { manifestFacts } from "./ManifestHealth";

const stats = (raw: Record<string, unknown>, manifest_count?: number) => ({ manifest_count, raw }) as unknown as TableStatsRead;

describe("manifestFacts", () => {
  it("flags small manifests, many manifests and metadata files that never get deleted", () => {
    const facts = manifestFacts(
      stats({
        manifest_stats: { manifest_count: 240, avg_manifest_bytes: 40_000, avg_files_per_manifest: 3, manifest_total_bytes: 9_600_000 },
        iceberg_properties: {}
      })
    );
    const byLabel = Object.fromEntries(facts.map((fact) => [fact.label, fact]));
    expect(byLabel["Manifests"].tone).toBe("warn");
    expect(byLabel["Average manifest"].tone).toBe("warn");
    expect(byLabel["Files per manifest"].tone).toBe("warn");
    expect(byLabel["Old metadata.json files"]).toMatchObject({ value: "Never deleted", tone: "warn" });
    expect(byLabel["Unreadable manifests"]).toBeUndefined();
  });

  it("reads the table's metadata retention settings and manifest read errors", () => {
    const facts = manifestFacts(
      stats(
        {
          iceberg_properties: {
            "write.metadata.delete-after-commit.enabled": "true",
            "write.metadata.previous-versions-max": "20"
          },
          manifest_read_errors: ["s3://bucket/m1.avro: 403"]
        },
        12
      )
    );
    const byLabel = Object.fromEntries(facts.map((fact) => [fact.label, fact]));
    expect(byLabel["Manifests"]).toMatchObject({ value: "12", tone: "ok" });
    expect(byLabel["Old metadata.json files"]).toMatchObject({ value: "Keeps 20", tone: "ok" });
    expect(byLabel["Unreadable manifests"].value).toBe("1");
  });

  it("shows nothing before a scan recorded manifests", () => {
    expect(manifestFacts(stats({}))).toEqual([]);
  });
});
