import type { EngineType } from "../api/types";

export const ENGINE_TYPE_OPTIONS: { label: string; value: EngineType }[] = [
  { label: "Trino", value: "trino" },
  { label: "Spark EMR", value: "spark_emr" },
  { label: "Native (iceberg-rust)", value: "native" }
];

const ENGINE_LABELS: Record<EngineType, string> = {
  trino: "Trino",
  spark_emr: "Spark EMR",
  native: "Native (iceberg-rust)"
};

const ENGINE_TAG_COLORS: Record<EngineType, string> = {
  trino: "blue",
  spark_emr: "orange",
  native: "purple"
};

export const formatEngineLabel = (engine: string | null | undefined): string => {
  if (!engine) return "—";
  if (engine in ENGINE_LABELS) return ENGINE_LABELS[engine as EngineType];
  return engine.replace(/_/g, " ");
};

export const engineTagColor = (engine: string | null | undefined): string => {
  if (engine && engine in ENGINE_TAG_COLORS) return ENGINE_TAG_COLORS[engine as EngineType];
  return "default";
};

export const isNativeStatement = (sql: string | null | undefined): boolean =>
  Boolean(sql?.startsWith("NATIVE:"));
