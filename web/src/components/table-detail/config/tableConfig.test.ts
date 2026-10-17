import { describe, expect, it } from "vitest";

import type { TableSettingRead } from "../../../api/types";
import {
  describeApplyError,
  formatBytes,
  formatDurationMs,
  formatValue,
  matchesFilter,
  pickUnit,
  toChanges,
  unitsFor
} from "./tableConfig";

const editor = (value_type: TableSettingRead["editor"]["value_type"], unit: string | null = null) => ({
  editor: { value_type, unit, options: [], minimum: null, maximum: null, allow_unset: true },
  default_label: null,
  default_value: null
});

describe("table config formatting", () => {
  it("formats bytes in binary units", () => {
    expect(formatBytes(536_870_912)).toBe("512 MiB");
    expect(formatBytes(1_073_741_824)).toBe("1 GiB");
    expect(formatBytes(67_108_864)).toBe("64 MiB");
    expect(formatBytes(2048)).toBe("2 KiB");
  });

  it("formats durations as whole days or hours when exact", () => {
    expect(formatDurationMs(432_000_000)).toBe("5 days");
    expect(formatDurationMs(86_400_000)).toBe("1 day");
    expect(formatDurationMs(7_200_000)).toBe("2 hours");
    expect(formatDurationMs(1_500)).toBe("1.5 s");
    expect(formatDurationMs(100)).toBe("100 ms");
  });

  it("formats raw values by type and falls back to the default for unset", () => {
    expect(formatValue(editor("bytes"), "536870912")).toBe("512 MiB");
    expect(formatValue(editor("duration_days"), "7")).toBe("7 days");
    expect(formatValue(editor("bool"), "TRUE")).toBe("Enabled");
    expect(formatValue(editor("columns"), "a,b")).toBe("a, b");
    expect(formatValue(editor("int", "ms"), "60000")).toBe("60 s");
    expect(formatValue(editor("int"), "4000")).toBe("4,000");
    expect(formatValue({ ...editor("text"), default_label: "Forever" }, null)).toBe("Forever");
    expect(formatValue({ ...editor("bytes"), default_value: "134217728" }, null)).toBe("128 MiB");
    expect(formatValue(editor("columns"), "")).toBe("None");
  });
});

describe("number editor units", () => {
  it("offers MiB/GiB for bytes and picks the largest exact unit", () => {
    const units = unitsFor(editor("bytes"));
    expect(units.map((u) => u.label)).toEqual(["MiB", "GiB"]);
    expect(pickUnit(units, 1_073_741_824).label).toBe("GiB");
    expect(pickUnit(units, 536_870_912).label).toBe("MiB");
  });

  it("offers hours and days for millisecond durations", () => {
    const units = unitsFor(editor("duration_ms"));
    expect(pickUnit(units, 432_000_000).label).toBe("days");
    expect(pickUnit(units, 7_200_000).label).toBe("hours");
  });
});

describe("staging", () => {
  it("sends every staged change with the value the user saw", () => {
    expect(
      toChanges({ "gc.enabled": { value: "false", expected: "true" }, comment: { value: null, expected: "x" } })
    ).toEqual([
      { key: "gc.enabled", value: "false", expected_value: "true", check_expected: true },
      { key: "comment", value: null, expected_value: "x", check_expected: true }
    ]);
  });

  it("filters to settings that differ from the recommendation", () => {
    const base = { is_set: true, current_source: "table" } as TableSettingRead;
    expect(matchesFilter({ ...base, matches_recommendation: false }, "differs")).toBe(true);
    expect(matchesFilter({ ...base, matches_recommendation: null }, "differs")).toBe(false);
    expect(matchesFilter({ ...base, current_source: "metadata" }, "set")).toBe(false);
  });

  it("explains blocked and override errors from the API detail", () => {
    const blocked = Object.assign(new Error("Bad Request"), {
      detail: { blocked: [{ key: "format-version", reason: "Downgrades are not possible" }] }
    });
    expect(describeApplyError(blocked)).toBe("format-version: Downgrades are not possible");
    const params = Object.assign(new Error("Bad Request"), { detail: { param_overrides: { retain_last: "must be >= 1" } } });
    expect(describeApplyError(params)).toBe("retain_last: must be >= 1");
    expect(describeApplyError(new Error("Catalog commit failed"))).toBe("Catalog commit failed");
  });
});
