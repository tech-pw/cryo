import type { DestinationRead, EngineType, JsonObject } from "../api/types";

export type NativeEngineImpl = "go" | "rust";

export const NATIVE_ENGINE_IMPL_DEFAULT: NativeEngineImpl = "go";

export type EngineTypeOption = {
  label: string;
  value: EngineType;
  nativeImpl?: NativeEngineImpl;
};

export const ENGINE_TYPE_OPTIONS: EngineTypeOption[] = [
  { label: "Trino", value: "trino" },
  { label: "Spark EMR", value: "spark_emr" },
  { label: "Native (iceberg-go)", value: "native", nativeImpl: "go" },
  { label: "Native (iceberg-rust)", value: "native", nativeImpl: "rust" }
];

export const ENGINE_VIRTUAL_OPTIONS = [
  { label: "Trino", value: "trino" },
  { label: "Spark EMR", value: "spark_emr" },
  { label: "Native (iceberg-go)", value: "native:go" },
  { label: "Native (iceberg-rust)", value: "native:rust" }
] as const;

export type EngineVirtualValue = (typeof ENGINE_VIRTUAL_OPTIONS)[number]["value"];

export const NATIVE_UNSUPPORTED_OPS = ["optimize_manifests"] as const;

export const nativeUnsupportedOpTooltip =
  "Not supported on the native engine; use a Trino or Spark preset for manifest rewrite.";

export const nativeRustOptimizeTooltip =
  "Compaction is not available on iceberg-rust; use Native (iceberg-go) or Trino/Spark.";

const ENGINE_TAG_COLORS: Record<EngineType, string> = {
  trino: "blue",
  spark_emr: "orange",
  native: "purple"
};

export const resolveNativeEngineImpl = (config?: JsonObject | null): NativeEngineImpl => {
  const configured = config?.engine_impl;
  if (configured === "go" || configured === "rust") return configured;
  return NATIVE_ENGINE_IMPL_DEFAULT;
};

export const formatNativeImplLabel = (impl: NativeEngineImpl): string =>
  impl === "go" ? "iceberg-go" : "iceberg-rust";

export const formatEngineLabel = (
  engine: string | null | undefined,
  config?: JsonObject | null
): string => {
  if (!engine) return "—";
  if (engine === "trino") return "Trino";
  if (engine === "spark_emr") return "Spark EMR";
  if (engine === "native") {
    return `Native (${formatNativeImplLabel(resolveNativeEngineImpl(config))})`;
  }
  return engine.replace(/_/g, " ");
};

export const formatDestinationLabel = (destination: DestinationRead): string =>
  `${destination.name} · ${formatEngineLabel(destination.engine, destination.config)}`;

export const isNativeDestination = (destination: DestinationRead | null | undefined): boolean =>
  destination?.engine === "native";

export const nativeSupportsOperation = (
  operation: string,
  impl: NativeEngineImpl = NATIVE_ENGINE_IMPL_DEFAULT
): boolean => {
  if (operation === "optimize_manifests") return false;
  if (operation === "optimize" && impl === "rust") return false;
  return true;
};

export const engineVirtualFromDestination = (
  engine: EngineType,
  config?: JsonObject | null
): EngineVirtualValue => {
  if (engine === "native") {
    return resolveNativeEngineImpl(config) === "rust" ? "native:rust" : "native:go";
  }
  return engine as EngineVirtualValue;
};

export const parseEngineVirtual = (
  virtual: string
): { engine: EngineType; engine_impl?: NativeEngineImpl } => {
  if (virtual === "native:go") return { engine: "native", engine_impl: "go" };
  if (virtual === "native:rust") return { engine: "native", engine_impl: "rust" };
  return { engine: virtual as EngineType };
};

export const engineTagColor = (engine: string | null | undefined): string => {
  if (engine && engine in ENGINE_TAG_COLORS) return ENGINE_TAG_COLORS[engine as EngineType];
  return "default";
};

export const isNativeStatement = (sql: string | null | undefined): boolean =>
  Boolean(sql?.startsWith("NATIVE:"));
