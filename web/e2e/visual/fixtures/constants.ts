export const VISUAL_NOW_ISO = "2026-09-28T04:30:00.000Z";
export const VISUAL_TIMEZONE = "Asia/Kolkata";
export const VISUAL_SEED = 20260928;
export const STALE_AS_OF = "2026-09-20T04:30:00.000Z";
export const PREVIOUS_YEAR_COMMITTED_AT = "2025-08-15T04:30:00.000Z";

export const TINY_SIZE_BYTES = 40 * 1024 * 1024;
export const TINY_SIZE_GB = 40 / 1024;

export const TABLE_LIST_TOTAL = 10_000;
export const JOB_LIST_TOTAL = 1_500;
export const SNAPSHOT_HISTORY_TOTAL = 240;
export const DEFAULT_TABLE_PAGE_SIZE = 50;
export const DEFAULT_JOB_PAGE_SIZE = 50;
export const DEFAULT_SNAPSHOT_PAGE_SIZE = 25;
export const MAX_PAGE_SIZE = 100;

export const LONG_SCHEMA_NAME = `visual_schema_${"visual-segment-".repeat(8)}`;
export const LONG_TABLE_NAME = `visual_table_${"visual-segment-".repeat(8)}`;

// 64-bit Iceberg id (Long.MAX_VALUE). Kept as a decimal string so JSON does not round past 2^53.
export const SNAPSHOT_ID_ABOVE_MAX_SAFE_INTEGER = "9223372036854775807";

export const VISUAL_SOURCE_ID = "00000000-0000-4000-8000-000000000001";
export const VISUAL_TRINO_DESTINATION_ID = "00000000-0000-4000-8000-000000000002";
export const VISUAL_FLINK_DESTINATION_ID = "00000000-0000-4000-8000-000000000003";
export const VISUAL_TABLE_ID = "00000000-0000-4000-8000-000000000010";
export const VISUAL_PRESET_ID = "00000000-0000-4000-8000-000000000020";
export const VISUAL_RUN_ID = "00000000-0000-4000-8000-000000000100";

export const VISUAL_SUBJECT = "visual-e2e";

export type VisualRole = "viewer" | "operator" | "admin";

export type TraitMode = "none" | "first" | "all";

export interface TraitFlags {
  unknown: boolean;
  partial: boolean;
  stale: boolean;
  longNames: TraitMode;
  previousYear: TraitMode;
  tinyBytes: TraitMode;
  unsupportedEngine: boolean;
}

export const NEUTRAL_TRAITS: TraitFlags = {
  unknown: false,
  partial: false,
  stale: false,
  longNames: "none",
  previousYear: "none",
  tinyBytes: "none",
  unsupportedEngine: false
};

export function visualUuid(index: number): string {
  return `00000000-0000-4000-8000-${index.toString(16).padStart(12, "0")}`;
}

export function visualAccessToken(role: VisualRole): string {
  const encode = (value: object): string => Buffer.from(JSON.stringify(value), "utf8").toString("base64");
  const header = encode({ alg: "none", typ: "JWT" });
  const payload = encode({
    sub: VISUAL_SUBJECT,
    preferred_username: VISUAL_SUBJECT,
    roles: [role],
    exp: 1893456000
  });
  return `${header}.${payload}.visual`;
}

export function snapshotIdExceedsJsSafeInteger(id: string): boolean {
  return BigInt(id) > 2n ** 53n;
}
