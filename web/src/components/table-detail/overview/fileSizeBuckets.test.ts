import { describe, expect, it } from "vitest";
import { bandFor, buildSizeHistogram } from "./fileSizeBuckets";

const MIB = 2 ** 20;

const bins = (entries: Record<number, number>) => {
  const data = new Array(40).fill(0);
  for (const [index, count] of Object.entries(entries)) data[Number(index)] = count;
  return { data, delete: new Array(40).fill(0), unknown: 0 };
};

describe("buildSizeHistogram", () => {
  it("returns null for missing or zero-filled bins instead of an empty chart", () => {
    expect(buildSizeHistogram({ raw: {} })).toBeNull();
    expect(buildSizeHistogram({ total_files: 10, raw: { file_size_bins: bins({}) } })).toBeNull();
  });

  it("groups sub-MiB files, bands against the configured target, and keeps every file", () => {
    const histogram = buildSizeHistogram({
      raw: { reduction_target_bytes: 256 * MIB, file_size_bins: bins({ 12: 11, 19: 4, 25: 183, 27: 97 }) }
    });
    expect(histogram).not.toBeNull();
    expect(histogram!.targetAssumed).toBe(false);
    expect(histogram!.buckets[0]).toMatchObject({ label: "<1M", files: 15, band: "small" });
    expect(histogram!.totalFiles).toBe(295);
    expect(histogram!.buckets.reduce((sum, bucket) => sum + bucket.files, 0)).toBe(295);
    const target = histogram!.buckets.find((bucket) => bucket.containsTarget);
    expect(target).toMatchObject({ label: "128M–256M", files: 97, band: "target" });
    expect(histogram!.byBand.target).toBe(97);
  });

  it("falls back to the Iceberg 512 MiB default and says so", () => {
    const histogram = buildSizeHistogram({ raw: { file_size_bins: bins({ 20: 3 }) } });
    expect(histogram!.targetBytes).toBe(512 * MIB);
    expect(histogram!.targetAssumed).toBe(true);
  });

  it("bands relative to the target", () => {
    const target = 256 * MIB;
    expect(bandFor(16 * MIB, 32 * MIB, target)).toBe("small");
    expect(bandFor(64 * MIB, 128 * MIB, target)).toBe("below");
    expect(bandFor(128 * MIB, 256 * MIB, target)).toBe("target");
    expect(bandFor(512 * MIB, 1024 * MIB, target)).toBe("over");
  });
});
