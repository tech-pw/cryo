import type {
  ConfigChange,
  ConfigRisk,
  MaintenanceOperationRead,
  PropertyDetailRead,
  TableSettingRead
} from "../../../api/types";

export type ValueSource = TableSettingRead["current_source"];
export type ValueType = TableSettingRead["editor"]["value_type"];

const KIB = 1024;
const MIB = KIB * 1024;
const GIB = MIB * 1024;
const HOUR_MS = 3_600_000;
const DAY_MS = 24 * HOUR_MS;

export const RISK_LABEL: Record<ConfigRisk, string> = { low: "Low", medium: "Medium", high: "High" };
export const RISK_RANK: Record<ConfigRisk, number> = { low: 0, medium: 1, high: 2 };

export const SOURCE_LABEL: Record<ValueSource, string> = {
  table: "Set on table",
  iceberg_default: "Iceberg default",
  engine_default: "Engine decides",
  metadata: "Table metadata",
  cryo_preset: "Cryo preset",
  cryo_override: "Table override",
  cryo_default: "Cryo default"
};

export const SOURCE_HINT: Record<ValueSource, string> = {
  table: "Written as a property on this table.",
  iceberg_default: "Not set on the table, so Iceberg's library default applies.",
  engine_default: "Not set on the table, so each writing engine picks its own behaviour.",
  metadata: "Part of the table's metadata file, not a property.",
  cryo_preset: "Comes from the Cryo preset that governs this table.",
  cryo_override: "Pinned for this table only. Survives preset reassignment.",
  cryo_default: "Cryo's built-in default; neither the preset nor an override sets it."
};

const trimNumber = (value: number): string =>
  Number.isInteger(value) ? String(value) : value.toFixed(value < 10 ? 2 : 1).replace(/\.?0+$/, "");

export const formatBytes = (bytes: number): string => {
  if (bytes >= GIB && bytes % (GIB / 4) === 0) return `${trimNumber(bytes / GIB)} GiB`;
  if (bytes >= MIB) return `${trimNumber(bytes / MIB)} MiB`;
  if (bytes >= KIB) return `${trimNumber(bytes / KIB)} KiB`;
  return `${bytes} B`;
};

export const formatDurationMs = (ms: number): string => {
  if (ms >= DAY_MS && ms % DAY_MS === 0) return plural(ms / DAY_MS, "day");
  if (ms >= HOUR_MS && ms % HOUR_MS === 0) return plural(ms / HOUR_MS, "hour");
  if (ms >= DAY_MS) return `${trimNumber(ms / DAY_MS)} days`;
  if (ms >= HOUR_MS) return `${trimNumber(ms / HOUR_MS)} hours`;
  if (ms >= 1000) return `${trimNumber(ms / 1000)} s`;
  return `${ms} ms`;
};

const plural = (count: number, unit: string): string => `${trimNumber(count)} ${unit}${count === 1 ? "" : "s"}`;

const asInt = (value: string): number | null => {
  if (!/^-?\d+$/.test(value.trim())) return null;
  return Number(value.trim());
};

/** Human form of a raw value; `null` means "unset" and falls back to the default. */
type FormattableSetting = Pick<TableSettingRead, "editor" | "default_label" | "default_value"> &
  Partial<Pick<TableSettingRead, "kind">>;

export const formatValue = (setting: FormattableSetting, value: string | null | undefined): string => {
  if (value == null || value === "") {
    if (setting.default_label) return setting.default_label;
    if (setting.default_value != null && setting.default_value !== "") {
      return formatValue({ ...setting, default_label: null, default_value: null }, setting.default_value);
    }
    return setting.editor.value_type === "columns" ? "None" : "Not set";
  }
  const type = setting.editor.value_type;
  const number = asInt(value);
  if (setting.kind === "format_version") return `v${value}`;
  if (type === "bytes" && number != null) return formatBytes(number);
  if (type === "duration_ms" && number != null) return formatDurationMs(number);
  if (type === "duration_days" && number != null) return plural(number, "day");
  if (type === "int" && number != null && setting.editor.unit === "ms") return formatDurationMs(number);
  if (type === "int" && number != null) return number.toLocaleString("en-US");
  if (type === "bool") return value.toLowerCase() === "true" ? "Enabled" : "Disabled";
  if (type === "columns") return value.split(",").filter(Boolean).join(", ");
  return value;
};

export interface UnitOption {
  id: string;
  label: string;
  factor: number;
}

/** Units the number editor offers. The API always receives the base unit. */
export const unitsFor = (setting: Pick<TableSettingRead, "editor">): UnitOption[] => {
  const { value_type: type, unit } = setting.editor;
  if (type === "bytes") {
    return [
      { id: "mib", label: "MiB", factor: MIB },
      { id: "gib", label: "GiB", factor: GIB }
    ];
  }
  if (type === "duration_ms") {
    return [
      { id: "hours", label: "hours", factor: HOUR_MS },
      { id: "days", label: "days", factor: DAY_MS }
    ];
  }
  if (type === "duration_days") return [{ id: "days", label: "days", factor: 1 }];
  if (unit === "ms") {
    return [
      { id: "ms", label: "ms", factor: 1 },
      { id: "s", label: "seconds", factor: 1000 },
      { id: "min", label: "minutes", factor: 60_000 }
    ];
  }
  return [{ id: "n", label: "", factor: 1 }];
};

/** Largest unit that represents `base` without a fraction, so editing starts on a clean number. */
export const pickUnit = (units: UnitOption[], base: number | null): UnitOption => {
  const fallback = units[units.length - 1] ?? { id: "n", label: "", factor: 1 };
  if (base == null) return units.length > 1 && units[0].id === "mib" ? units[0] : fallback;
  const exact = [...units].reverse().find((u) => base % u.factor === 0 && base >= u.factor);
  return exact ?? units[0] ?? fallback;
};

export const isNumericType = (type: ValueType): boolean =>
  type === "bytes" || type === "int" || type === "duration_ms" || type === "duration_days";

export const parseIntOrNull = (value: string | null | undefined): number | null =>
  value == null ? null : asInt(value);

export const rangeHint = (setting: Pick<TableSettingRead, "editor" | "default_label" | "default_value">): string | null => {
  const { minimum, maximum } = setting.editor;
  if (minimum == null && maximum == null) return null;
  const fmt = (n: number) => formatValue({ ...setting, default_label: null }, String(n));
  if (minimum != null && maximum != null) return `${fmt(minimum)} – ${fmt(maximum)}`;
  if (minimum != null) return `At least ${fmt(minimum)}`;
  return `At most ${fmt(maximum as number)}`;
};

/** A staged edit: the proposed raw value (`null` = unset) plus what the row showed when staged. */
export interface StagedChange {
  value: string | null;
  expected: string | null;
}

export type StagedChanges = Record<string, StagedChange>;

export const toChanges = (staged: StagedChanges): ConfigChange[] =>
  Object.entries(staged).map(([key, change]) => ({
    key,
    value: change.value,
    expected_value: change.expected,
    check_expected: true
  }));

export type SettingFilter = "all" | "differs" | "set";

export const matchesSearch = (setting: TableSettingRead, query: string): boolean => {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [setting.key, setting.label, setting.summary, setting.current_value ?? ""]
    .some((hay) => hay.toLowerCase().includes(needle));
};

export const matchesFilter = (setting: TableSettingRead, filter: SettingFilter): boolean => {
  if (filter === "differs") return setting.matches_recommendation === false;
  if (filter === "set") return setting.is_set && setting.current_source !== "metadata";
  return true;
};

export const ENGINE_LABEL: Record<string, string> = {
  trino: "Trino",
  spark: "Spark",
  flink: "Flink",
  native: "Native (Go)",
  cryo: "Cryo"
};

export const settingAnchor = (key: string): string => `tc-setting-${key.replace(/[^a-zA-Z0-9_-]/g, "-")}`;

export const PROPERTY_KIND_LABEL: Record<PropertyDetailRead["kind"], string> = {
  setting: "Cryo setting",
  read: "Read",
  write: "Write",
  behavior: "Table behavior",
  reserved: "Reserved",
  catalog: "Catalog",
  engine: "Engine",
  custom: "Custom"
};

export const matchesProperty = (detail: PropertyDetailRead, query: string): boolean => {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [detail.key, detail.value, detail.summary].some((text) => text.toLowerCase().includes(needle));
};

type OperationStatus = MaintenanceOperationRead["status"];

export const OPERATION_STATUS_LABEL: Record<OperationStatus, string> = {
  runs: "Runs",
  when_needed: "When needed",
  not_in_mode: "Not in this mode",
  off: "Off in preset",
  blocked: "Blocked",
  held: "Table on hold",
  excluded: "Excluded",
  not_scheduled: "Not scheduled",
  unsupported: "Unsupported here"
};

export const OPERATION_STATUS_TONE: Record<OperationStatus, "ok" | "muted" | "warn" | "danger"> = {
  runs: "ok",
  when_needed: "ok",
  not_in_mode: "muted",
  off: "muted",
  not_scheduled: "muted",
  blocked: "warn",
  held: "warn",
  unsupported: "warn",
  excluded: "danger"
};

export interface OperationDiff {
  exclude: string[];
  include: string[];
}

export const diffExclusions = (operations: MaintenanceOperationRead[], draft: ReadonlySet<string>): OperationDiff => ({
  exclude: operations.filter((op) => !op.excluded && draft.has(op.operation)).map((op) => op.operation),
  include: operations.filter((op) => op.excluded && !draft.has(op.operation)).map((op) => op.operation)
});

/** A companion hint applies when its operation is excluded but the one it names still runs. */
export const companionWarnings = (
  operations: MaintenanceOperationRead[],
  draft: ReadonlySet<string>
): { operation: string; hint: string }[] =>
  operations.flatMap((op) =>
    op.companion_hint && op.companion_operation && draft.has(op.operation) && !draft.has(op.companion_operation)
      ? [{ operation: op.operation, hint: op.companion_hint }]
      : []
  );

/** Turns an API error `detail` object (blocked list, param errors) into readable text. */
export const describeApplyError = (error: unknown): string => {
  if (!(error instanceof Error)) return "Request failed";
  const detail = (error as Error & { detail?: unknown }).detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const record = detail as Record<string, unknown>;
    if (Array.isArray(record.blocked)) {
      return record.blocked
        .map((item) => {
          const entry = item as { key?: string; reason?: string };
          return `${entry.key ?? "change"}: ${entry.reason ?? "blocked"}`;
        })
        .join("; ");
    }
    if (record.param_overrides && typeof record.param_overrides === "object") {
      return Object.entries(record.param_overrides as Record<string, unknown>)
        .map(([key, value]) => `${key}: ${String(value)}`)
        .join("; ");
    }
  }
  return error.message || "Request failed";
};
