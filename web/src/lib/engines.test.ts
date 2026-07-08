import { describe, expect, it } from "vitest";
import {
  ENGINE_TYPE_OPTIONS,
  ENGINE_VIRTUAL_OPTIONS,
  engineTagColor,
  engineVirtualFromDestination,
  formatDestinationLabel,
  formatEngineLabel,
  isNativeDestination,
  isNativeStatement,
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
      "Native (iceberg-rust)"
    ]);
    expect(ENGINE_VIRTUAL_OPTIONS.map((option) => option.value)).toEqual([
      "trino",
      "spark_emr",
      "native:go",
      "native:rust"
    ]);
  });

  it("formats engine labels with native implementation", () => {
    expect(formatEngineLabel("trino")).toBe("Trino");
    expect(formatEngineLabel("spark_emr")).toBe("Spark EMR");
    expect(formatEngineLabel("native")).toBe("Native (iceberg-go)");
    expect(formatEngineLabel("native", { engine_impl: "go" })).toBe("Native (iceberg-go)");
    expect(formatEngineLabel("native", { engine_impl: "rust" })).toBe("Native (iceberg-rust)");
    expect(formatEngineLabel(null)).toBe("—");
  });

  it("resolves native implementation defaults", () => {
    expect(resolveNativeEngineImpl()).toBe("go");
    expect(resolveNativeEngineImpl({})).toBe("go");
    expect(resolveNativeEngineImpl({ engine_impl: "rust" })).toBe("rust");
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
    expect(nativeSupportsOperation("optimize", "rust")).toBe(false);
  });

  it("maps virtual engine values", () => {
    expect(engineVirtualFromDestination("native", { engine_impl: "go" })).toBe("native:go");
    expect(engineVirtualFromDestination("native", { engine_impl: "rust" })).toBe("native:rust");
    expect(parseEngineVirtual("native:go")).toEqual({ engine: "native", engine_impl: "go" });
    expect(parseEngineVirtual("trino")).toEqual({ engine: "trino" });
  });

  it("maps tag colors per engine", () => {
    expect(engineTagColor("trino")).toBe("blue");
    expect(engineTagColor("spark_emr")).toBe("orange");
    expect(engineTagColor("native")).toBe("purple");
  });

  it("detects native pseudo-sql statements", () => {
    expect(isNativeStatement("NATIVE:optimize:db.table")).toBe(true);
    expect(isNativeStatement("EXECUTE optimize")).toBe(false);
  });
});
