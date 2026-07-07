import { describe, expect, it } from "vitest";
import { ENGINE_TYPE_OPTIONS, engineTagColor, formatEngineLabel, isNativeStatement } from "./engines";

describe("engines", () => {
  it("lists all maintenance engine types", () => {
    expect(ENGINE_TYPE_OPTIONS.map((option) => option.value)).toEqual(["trino", "spark_emr", "native"]);
  });

  it("formats engine labels", () => {
    expect(formatEngineLabel("trino")).toBe("Trino");
    expect(formatEngineLabel("spark_emr")).toBe("Spark EMR");
    expect(formatEngineLabel("native")).toBe("Native (iceberg-rust)");
    expect(formatEngineLabel(null)).toBe("—");
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
