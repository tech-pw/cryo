import { describe, expect, it } from "vitest";
import {
  ENGINE_TYPE_OPTIONS,
  ENGINE_VIRTUAL_OPTIONS,
  engineTagColor,
  engineTypeOptions,
  engineVirtualFromDestination,
  engineVirtualOptions,
  formatDestinationLabel,
  formatEngineLabel,
  hasSqlFallbackConfigured,
  isNativeDestination,
  isNativeStatement,
  nativeEngineOptionLabel,
  nativeFleetDisabledRowLabel,
  nativeSupportsOperation,
  parseEngineVirtual,
  resolveNativeEngineImpl
} from "./engines";
import type { DestinationRead } from "../api/types";

const nativeGoDestination: DestinationRead = {
  id: "dest-1",
  name: "native-datafusion",
  engine: "native",
  config: { engine_impl: "go", slot_cap: 5 },
  enabled: true,
  created_at: "",
  updated_at: ""
};

describe("engines", () => {
  it("lists all maintenance engine types including split native options", () => {
    expect(ENGINE_TYPE_OPTIONS.map((option) => option.label)).toEqual([
      "Trino",
      "Spark EMR",
      "Native (iceberg-go)",
      "Flink (config only)"
    ]);
    expect(ENGINE_VIRTUAL_OPTIONS.map((option) => option.value)).toEqual([
      "trino",
      "spark_emr",
      "native:go",
      "flink"
    ]);
  });

  it("formats engine labels with native implementation", () => {
    expect(formatEngineLabel("trino")).toBe("Trino");
    expect(formatEngineLabel("spark_emr")).toBe("Spark EMR");
    expect(formatEngineLabel("flink")).toBe("Flink (config only)");
    expect(formatEngineLabel("native")).toBe("Native (iceberg-go)");
    expect(formatEngineLabel("native", { engine_impl: "go" })).toBe("Native (iceberg-go)");
    // A config left over from the removed Rust engine still reads as iceberg-go.
    expect(formatEngineLabel("native", { engine_impl: "rust" })).toBe("Native (iceberg-go)");
    expect(formatEngineLabel(null)).toBe("—");
    expect(formatEngineLabel("native", { engine_impl: "go" }, { nativeEnabled: true })).toBe(
      "Native (iceberg-go)"
    );
    expect(formatEngineLabel("native", { engine_impl: "go" }, {})).toBe("Native (iceberg-go)");
    expect(formatEngineLabel("native", { engine_impl: "go" }, { nativeEnabled: false })).toBe(
      "Native (iceberg-go) (disabled fleet-wide)"
    );
  });

  it("labels native disabled fleet-wide when the flag is false", () => {
    expect(nativeEngineOptionLabel(false)).toBe("Native (iceberg-go) (disabled fleet-wide)");
    expect(nativeEngineOptionLabel(true)).toBe("Native (iceberg-go)");
    expect(engineTypeOptions().map((option) => option.label)).toEqual(
      ENGINE_TYPE_OPTIONS.map((option) => option.label)
    );
    expect(engineTypeOptions({ nativeEnabled: true }).map((option) => option.label)).toEqual(
      ENGINE_TYPE_OPTIONS.map((option) => option.label)
    );
    expect(engineTypeOptions({ nativeEnabled: false }).map((option) => option.label)).toEqual([
      "Trino",
      "Spark EMR",
      "Native (iceberg-go) (disabled fleet-wide)",
      "Flink (config only)"
    ]);
    expect(engineVirtualOptions().map((option) => option.label)).toEqual(
      ENGINE_VIRTUAL_OPTIONS.map((option) => option.label)
    );
    expect(engineVirtualOptions({ nativeEnabled: false }).map((option) => option.label)).toEqual([
      "Trino",
      "Spark EMR",
      "Native (iceberg-go) (disabled fleet-wide)",
      "Flink (config only)"
    ]);
    expect(nativeFleetDisabledRowLabel("the configured Spark SQL fallback")).toBe(
      "Disabled fleet-wide — work runs on the configured Spark SQL fallback"
    );
  });

  it("resolves native implementation defaults", () => {
    expect(resolveNativeEngineImpl()).toBe("go");
    expect(resolveNativeEngineImpl({})).toBe("go");
    expect(resolveNativeEngineImpl({ engine_impl: "rust" })).toBe("go");
  });

  it("formats destination labels", () => {
    expect(formatDestinationLabel(nativeGoDestination)).toBe(
      "native-datafusion · Native (iceberg-go)"
    );
  });

  it("detects native destinations and supported operations", () => {
    expect(isNativeDestination(nativeGoDestination)).toBe(true);
    expect(nativeSupportsOperation("expire_snapshots", "go")).toBe(true);
    expect(nativeSupportsOperation("optimize", "go")).toBe(true);
    expect(nativeSupportsOperation("optimize_manifests", "go")).toBe(false);
    expect(nativeSupportsOperation("compute_statistics", "go")).toBe(false);
    expect(nativeSupportsOperation("rewrite_position_deletes", "go")).toBe(false);
  });

  it("maps virtual engine values", () => {
    expect(engineVirtualFromDestination("native", { engine_impl: "go" })).toBe("native:go");
    expect(engineVirtualFromDestination("native", { engine_impl: "rust" })).toBe("native:go");
    expect(parseEngineVirtual("native:go")).toEqual({ engine: "native", engine_impl: "go" });
    expect(parseEngineVirtual("trino")).toEqual({ engine: "trino" });
    expect(parseEngineVirtual("flink")).toEqual({ engine: "flink" });
  });

  it("maps tag colors per engine", () => {
    expect(engineTagColor("trino")).toBe("blue");
    expect(engineTagColor("spark_emr")).toBe("orange");
    expect(engineTagColor("native")).toBe("purple");
    expect(engineTagColor("flink")).toBe("gray");
  });

  it("detects native pseudo-sql statements", () => {
    expect(isNativeStatement("NATIVE:optimize:db.table")).toBe(true);
    expect(isNativeStatement("EXECUTE optimize")).toBe(false);
  });

  it("detects a configured SQL fallback destination", () => {
    expect(hasSqlFallbackConfigured(undefined)).toBe(false);
    expect(hasSqlFallbackConfigured({})).toBe(false);
    expect(hasSqlFallbackConfigured({ sql_fallback_destination_id: "" })).toBe(false);
    expect(hasSqlFallbackConfigured({ sql_fallback_destination_id: "spark-1" })).toBe(true);
    expect(hasSqlFallbackConfigured({ fallback_destination_id: "spark-1" })).toBe(true);
  });
});
