import type { EngineType } from "../../api/types";
import {
  engineVirtualFromDestination,
  parseEngineVirtual,
  resolveNativeEngineImpl,
  type EngineVirtualValue,
  type NativeEngineImpl
} from "../../lib/engines";
import { parseCatalogBindings, type CatalogBindingFormValue } from "../../lib/catalogBindings";
import { REDACTED_PLACEHOLDER } from "./constants";
import { isRedactedPlaceholder } from "./helpers";

export interface TrinoCoordinatorFormValue {
  host: string;
  port: number;
  http_scheme: "http" | "https";
}

export interface DestinationFormValues {
  name: string;
  engine: EngineType;
  engine_virtual: EngineVirtualValue;
  engine_impl?: NativeEngineImpl;
  offpeak_start?: number;
  offpeak_end?: number;
  secret_ref?: string | null;
  enabled: boolean;
  coordinators?: TrinoCoordinatorFormValue[];
  user?: string;
  catalog?: string;
  schema?: string;
  catalog_bindings?: CatalogBindingFormValue[];
  use_basic_auth?: boolean;
  auth_username?: string;
  auth_password?: string;
  /** True when the saved config already has a basic-auth password (not re-sent by the API — see redact.py). */
  auth_has_password?: boolean;
  spark_submit_mode?: "emr" | "k8s";
  application_id?: string;
  execution_role_arn?: string;
  region?: string;
  s3_logs_bucket?: string;
  s3_logs_prefix?: string;
  entry_point?: string;
  poll_interval_seconds?: number;
  timeout_seconds?: number;
  spark_submit_params_json?: string;
  query_log_enabled?: boolean;
  query_log_mode?: "trino_system_runtime" | "read_through";
  query_log_external_table?: string;
  query_log_sample_rate?: number;
  query_log_pull_interval_seconds?: number;
  query_log_watermark_overlap_seconds?: number;
  query_log_telemetry_coordinators?: string;
  query_log_retention_days?: number;
  slot_cap?: number;
  operation_mode?: string;
  binary_path?: string;
}

export const decomposeDestinationConfig = (engine: string, config: Record<string, unknown> | undefined) => {
  const cfg = config ?? {};
  if (engine === "spark_emr") {
    const sparkParams = cfg.spark_submit_params;
    return {
      spark_submit_mode: cfg.submit_mode === "k8s" ? "k8s" : "emr",
      application_id: typeof cfg.application_id === "string" ? cfg.application_id : "",
      execution_role_arn: typeof cfg.execution_role_arn === "string" ? cfg.execution_role_arn : "",
      region: typeof cfg.region === "string" ? cfg.region : "us-east-1",
      s3_logs_bucket: typeof cfg.s3_logs_bucket === "string" ? cfg.s3_logs_bucket : "",
      s3_logs_prefix: typeof cfg.s3_logs_prefix === "string" ? cfg.s3_logs_prefix : "cryo-spark-logs",
      entry_point:
        typeof cfg.entry_point === "string" ? cfg.entry_point : "s3://cryo-artifacts/cryo_spark_runner.py",
      poll_interval_seconds: typeof cfg.poll_interval_seconds === "number" ? cfg.poll_interval_seconds : 10,
      timeout_seconds: typeof cfg.timeout_seconds === "number" ? cfg.timeout_seconds : 3600,
      spark_submit_params_json: sparkParams ? JSON.stringify(sparkParams, null, 2) : ""
    };
  }
  if (engine === "native") {
    const engine_impl = resolveNativeEngineImpl(cfg);
    return {
      engine_impl,
      engine_virtual: engineVirtualFromDestination("native", cfg),
      slot_cap: typeof cfg.slot_cap === "number" ? cfg.slot_cap : 5,
      operation_mode: typeof cfg.operation_mode === "string" ? cfg.operation_mode : "execute",
      timeout_seconds: typeof cfg.timeout_seconds === "number" ? cfg.timeout_seconds : 3600,
      binary_path: typeof cfg.binary_path === "string" ? cfg.binary_path : ""
    };
  }

  const rawCoordinators = Array.isArray(cfg.coordinators) ? cfg.coordinators : ["localhost"];
  const coordinators: TrinoCoordinatorFormValue[] = rawCoordinators.map((item) => {
    if (typeof item === "string") {
      return { host: item, port: 8080, http_scheme: "http" };
    }
    const record = item as Record<string, unknown>;
    return {
      host: typeof record.host === "string" ? record.host : "",
      port: typeof record.port === "number" ? record.port : 8080,
      http_scheme: record.http_scheme === "https" || record.scheme === "https" ? "https" : "http"
    };
  });
  const auth = cfg.auth as Record<string, unknown> | undefined;
  const rawAuthPassword = auth?.password;
  // Same redaction concern as sourceConfig.ts's s3_secret_access_key: never pre-fill the editable
  // password input with the literal "***REDACTED***" placeholder the API always sends back once a
  // basic-auth password has been saved (see cryo/api/redact.py).
  const auth_has_password = typeof rawAuthPassword === "string" && rawAuthPassword.length > 0;
  const auth_password =
    typeof rawAuthPassword === "string" && !isRedactedPlaceholder(rawAuthPassword) ? rawAuthPassword : "";
  const ql = (cfg as Record<string, unknown>).query_log as Record<string, unknown> | undefined;
  const qlCoordinators = Array.isArray(ql?.telemetry_coordinators)
    ? (ql!.telemetry_coordinators as string[]).join("\n")
    : "";
  return {
    coordinators,
    user: typeof cfg.user === "string" ? cfg.user : "cryo",
    catalog: typeof cfg.catalog === "string" ? cfg.catalog : "iceberg",
    schema: typeof cfg.schema === "string" ? cfg.schema : undefined,
    catalog_bindings: parseCatalogBindings(cfg),
    use_basic_auth: Boolean(auth && auth.type === "basic"),
    auth_username: typeof auth?.username === "string" ? auth.username : "",
    auth_password,
    auth_has_password,
    query_log_enabled: Boolean(ql && ql.enabled),
    query_log_mode: ql?.mode === "read_through" ? "read_through" : "trino_system_runtime",
    query_log_external_table: typeof ql?.external_table === "string" ? ql.external_table : "",
    query_log_sample_rate: typeof ql?.sample_rate === "number" ? ql.sample_rate : 1.0,
    query_log_pull_interval_seconds:
      typeof ql?.pull_interval_seconds === "number" ? ql.pull_interval_seconds : 300,
    query_log_watermark_overlap_seconds:
      typeof ql?.watermark_overlap_seconds === "number" ? ql.watermark_overlap_seconds : 900,
    query_log_telemetry_coordinators: qlCoordinators,
    query_log_retention_days: typeof ql?.retention_days === "number" ? ql.retention_days : 30
  };
};

export const buildDestinationConfig = (values: DestinationFormValues): Record<string, unknown> => {
  if (values.engine === "spark_emr") {
    const submitMode = values.spark_submit_mode === "k8s" ? "k8s" : "emr";
    const config: Record<string, unknown> = {
      submit_mode: submitMode,
      application_id: submitMode === "k8s" ? values.application_id || null : values.application_id,
      execution_role_arn: submitMode === "k8s" ? values.execution_role_arn || null : values.execution_role_arn,
      region: values.region || "us-east-1",
      s3_logs_prefix: values.s3_logs_prefix || "cryo-spark-logs",
      entry_point: values.entry_point || "s3://cryo-artifacts/cryo_spark_runner.py",
      poll_interval_seconds: values.poll_interval_seconds || 10,
      timeout_seconds: values.timeout_seconds || 3600
    };
    config.s3_logs_bucket = values.s3_logs_bucket || null;
    config.spark_submit_params = values.spark_submit_params_json?.trim()
      ? JSON.parse(values.spark_submit_params_json)
      : null;
    return config;
  }
  if (values.engine === "native") {
    const config: Record<string, unknown> = {
      engine_impl: values.engine_impl ?? "go",
      slot_cap: values.slot_cap ?? 5,
      operation_mode: values.operation_mode || "execute",
      timeout_seconds: values.timeout_seconds ?? 3600
    };
    config.binary_path = values.binary_path?.trim() || null;
    return config;
  }

  const config: Record<string, unknown> = {
    coordinators: (values.coordinators ?? []).map((c) => ({
      host: c.host,
      port: c.port || 8080,
      http_scheme: c.http_scheme || "http"
    })),
    user: values.user || "cryo",
    catalog: values.catalog || "iceberg"
  };
  config.schema = values.schema || null;
  const bindings = (values.catalog_bindings ?? [])
    .filter((b) => b?.source_id)
    .map((b) => {
      const entry: Record<string, string> = { source_id: b.source_id };
      if (b.trino_catalog?.trim()) entry.trino_catalog = b.trino_catalog.trim();
      return entry;
    });
  config.catalog_bindings = bindings;
  if (!values.use_basic_auth) {
    config.auth = null;
  } else if (values.use_basic_auth) {
    // The `auth` object is always sent in full (unlike the Source's flat config keys, there's no
    // way to omit just the password here), so a blank password must be resolved explicitly: if a
    // password was already saved and the field was left untouched, resubmit the redaction
    // sentinel so the backend's unredact_merge restores the real value instead of overwriting it
    // with an empty string (see cryo/api/redact.py / patch_destination).
    const password = values.auth_password
      ? values.auth_password
      : values.auth_has_password
        ? REDACTED_PLACEHOLDER
        : "";
    config.auth = { type: "basic", username: values.auth_username, password };
  }
  return config;
};

export const queryLogFormFields = (
  queryLog: Record<string, unknown> | null | undefined,
  retentionDays?: number | null
): Pick<
  DestinationFormValues,
  | "query_log_enabled"
  | "query_log_mode"
  | "query_log_external_table"
  | "query_log_sample_rate"
  | "query_log_pull_interval_seconds"
  | "query_log_watermark_overlap_seconds"
  | "query_log_telemetry_coordinators"
  | "query_log_retention_days"
> => {
  const ql = queryLog ?? undefined;
  const coordinators = Array.isArray(ql?.telemetry_coordinators)
    ? (ql.telemetry_coordinators as string[]).join("\n")
    : "";
  return {
    query_log_enabled: Boolean(ql && ql.enabled),
    query_log_mode: ql?.mode === "read_through" ? "read_through" : "trino_system_runtime",
    query_log_external_table: typeof ql?.external_table === "string" ? ql.external_table : "",
    query_log_sample_rate: typeof ql?.sample_rate === "number" ? ql.sample_rate : 1.0,
    query_log_pull_interval_seconds:
      typeof ql?.pull_interval_seconds === "number" ? ql.pull_interval_seconds : 300,
    query_log_watermark_overlap_seconds:
      typeof ql?.watermark_overlap_seconds === "number" ? ql.watermark_overlap_seconds : 900,
    query_log_telemetry_coordinators: coordinators,
    query_log_retention_days:
      typeof retentionDays === "number"
        ? retentionDays
        : typeof ql?.retention_days === "number"
          ? ql.retention_days
          : undefined
  };
};

export const buildQueryLogConfig = (values: DestinationFormValues): Record<string, unknown> | null => {
  if (!values.query_log_enabled) return null;
  const block: Record<string, unknown> = {
    enabled: true,
    mode: values.query_log_mode || "trino_system_runtime"
  };
  if (values.query_log_external_table) {
    block.external_table = values.query_log_external_table;
  }
  if (typeof values.query_log_sample_rate === "number") {
    block.sample_rate = values.query_log_sample_rate;
  }
  if (typeof values.query_log_pull_interval_seconds === "number") {
    block.pull_interval_seconds = values.query_log_pull_interval_seconds;
  }
  if (typeof values.query_log_watermark_overlap_seconds === "number") {
    block.watermark_overlap_seconds = values.query_log_watermark_overlap_seconds;
  }
  if (values.query_log_telemetry_coordinators?.trim()) {
    block.telemetry_coordinators = values.query_log_telemetry_coordinators
      .split(/[\n,]+/)
      .map((s) => s.trim())
      .filter(Boolean);
  }
  return block;
};

export { parseEngineVirtual };
