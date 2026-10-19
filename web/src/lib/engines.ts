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
  { label: "Native (iceberg-rust)", value: "native", nativeImpl: "rust" },
  { label: "Flink (config only)", value: "flink" }
];

export const ENGINE_VIRTUAL_OPTIONS = [
  { label: "Trino", value: "trino" },
  { label: "Spark EMR", value: "spark_emr" },
  { label: "Native (iceberg-go)", value: "native:go" },
  { label: "Native (iceberg-rust)", value: "native:rust" },
  { label: "Flink (config only)", value: "flink" }
] as const;

export type EngineVirtualValue = (typeof ENGINE_VIRTUAL_OPTIONS)[number]["value"];

export type EngineOptionFlags = {
  nativeEnabled?: boolean;
};

export const nativeEngineOptionLabel = (
  nativeEnabled: boolean,
  impl: NativeEngineImpl = NATIVE_ENGINE_IMPL_DEFAULT
): string => {
  const base = `Native (${formatNativeImplLabel(impl)})`;
  return nativeEnabled ? base : `${base} (disabled fleet-wide)`;
};

export const nativeFleetDisabledRowLabel = (fallback: string): string =>
  `Disabled fleet-wide — work runs on ${fallback}`;

export const engineTypeOptions = (flags: EngineOptionFlags = {}): EngineTypeOption[] => {
  if (flags.nativeEnabled !== false) return ENGINE_TYPE_OPTIONS;
  return ENGINE_TYPE_OPTIONS.map((option) =>
    option.value === "native"
      ? {
          ...option,
          label: nativeEngineOptionLabel(false, option.nativeImpl ?? NATIVE_ENGINE_IMPL_DEFAULT)
        }
      : option
  );
};

export const engineVirtualOptions = (
  flags: EngineOptionFlags = {}
): { label: string; value: EngineVirtualValue }[] => {
  if (flags.nativeEnabled !== false) return [...ENGINE_VIRTUAL_OPTIONS];
  return ENGINE_VIRTUAL_OPTIONS.map((option) => {
    if (option.value === "native:go") {
      return { ...option, label: nativeEngineOptionLabel(false, "go") };
    }
    if (option.value === "native:rust") {
      return { ...option, label: nativeEngineOptionLabel(false, "rust") };
    }
    return option;
  });
};

export const NATIVE_UNSUPPORTED_OPS = [
  "convert_deletes",
  "optimize_manifests",
  "compute_statistics",
  "rewrite_position_deletes",
  "expire_data"
] as const;

export const nativeUnsupportedOpTooltip =
  "Not executed by the native engine; configure a compatible Trino or Spark fallback.";

export const nativeRustOptimizeTooltip =
  "Compaction is not available on iceberg-rust; use Native (iceberg-go) or Trino/Spark.";

export const nativeGoDeleteFilesRewriteError =
  "Rewrite is blocked on iceberg-go for tables with delete files (iceberg-go bug). Snapshot expiry and orphan removal will still run.";

export const nativeGoDeleteFilesFallbackNotice =
  "Rewrite can't run on iceberg-go for tables with delete files (iceberg-go bug); routing it to the configured SQL fallback destination instead.";

/** True when a destination's config names a SQL fallback (Trino/Spark) for
 * operations the native engine can't run itself -- see cryo.engines.native.routing. */
export function hasSqlFallbackConfigured(config: JsonObject | null | undefined): boolean {
  if (!config) return false;
  const id = config["sql_fallback_destination_id"] ?? config["fallback_destination_id"];
  return typeof id === "string" && id.trim().length > 0;
}

const ENGINE_TAG_COLORS: Record<EngineType, string> = {
  trino: "blue",
  spark_emr: "orange",
  native: "purple",
  flink: "gray"
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
  config?: JsonObject | null,
  flags?: EngineOptionFlags
): string => {
  if (!engine) return "—";
  if (engine === "trino") return "Trino";
  if (engine === "spark_emr") {
    return config?.submit_mode === "k8s" ? "Spark on Kubernetes" : "Spark EMR";
  }
  if (engine === "flink") return "Flink (config only)";
  if (engine === "native") {
    const impl = resolveNativeEngineImpl(config);
    if (flags?.nativeEnabled === false) return nativeEngineOptionLabel(false, impl);
    return `Native (${formatNativeImplLabel(impl)})`;
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
  if ((NATIVE_UNSUPPORTED_OPS as readonly string[]).includes(operation)) return false;
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
