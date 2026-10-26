import { describe, expect, it } from "vitest";
import type { PresetRead } from "../api/types";
import {
  formValuesToPresetWrite,
  formatPresetSummary,
  presetToFormValues,
  type PresetFormValues
} from "./presetForm";

const destinationId = "00000000-0000-0000-0000-000000000001";

const baseFormValues = (): PresetFormValues => ({
  name: "standard",
  destination_id: destinationId,
  mode: "standard",
  operations: ["expire_snapshots", "optimize"],
  priority: 100,
  enabled: true,
  is_default: false,
  paused: false,
  min_small_files: 128,
  chunk_size_gb: 100,
  max_partitions_per_chunk: 100,
  delete_ratio_trigger: 0,
  partition_write_idle_hours: 0,
  retention_days: 7,
  retain_last: 1,
  file_size_mb: 256,
  compute_statistics: false,
  stats_compute_ndv: true,
  cooldown_hours: 24,
  sla_freshness_hours: 24,
  sla_maintenance_days: 7,
  sla_p95_latency_ms: 5000,
  adaptive_routing: false,
  spark_min_gb: 100,
  native_min_gb: 50,
  latency_p99_ms: 500
});

describe("presetToFormValues", () => {
  it("maps preset params and guardrails into explicit fields", () => {
    const preset: PresetRead = {
      id: "00000000-0000-0000-0000-000000000010",
      name: "large",
      destination_id: destinationId,
      mode: "standard",
      operations: ["optimize"],
      params: {
        min_small_files: 64,
        max_chunks: 3,
        stats_columns: ["id"]
      },
      guardrails: {
        cooldown_hours: 12,
        adaptive_routing: true,
        engine_preferences: { spark_min_gb: 200, native_min_gb: 25, latency_p99_ms: 750 },
        routing_rules: [
          {
            predicate: { field: "total_size_gb", op: ">", value: 100 },
            destination: "spark-large"
          }
        ]
      },
      priority: 90,
      enabled: true,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z"
    };

    const values = presetToFormValues(preset, []);
    expect(values.min_small_files).toBe(64);
    expect(values.max_chunks).toBe(3);
    expect(values.stats_columns).toEqual(["id"]);
    expect(values.cooldown_hours).toBe(12);
    expect(values.adaptive_routing).toBe(true);
    expect(values.spark_min_gb).toBe(200);
    expect(values.routing_rules).toEqual([
      { field: "total_size_gb", op: ">", value: 100, destination: "spark-large" }
    ]);
  });
});

describe("formValuesToPresetWrite", () => {
  it("builds API params and guardrails objects", () => {
    const body = formValuesToPresetWrite({
      ...baseFormValues(),
      max_chunks: 4,
      stats_columns: ["id", "created_at"],
      adaptive_routing: true,
      engine_pool: ["trino-main"],
      routing_rules: [
        { field: "total_size_gb", op: ">", value: 100, destination: "spark-large" }
      ]
    });

    expect(body.params.min_small_files).toBe(128);
    expect(body.params.max_chunks).toBe(4);
    expect(body.params.stats_columns).toEqual(["id", "created_at"]);
    expect(body.guardrails.adaptive_routing).toBe(true);
    expect(body.guardrails.engine_pool).toEqual(["trino-main"]);
    expect(body.guardrails.engine_preferences).toEqual({
      spark_min_gb: 100,
      native_min_gb: 50,
      latency_p99_ms: 500
    });
    expect(body.guardrails.routing_rules).toEqual([
      {
        predicate: { field: "total_size_gb", op: ">", value: 100 },
        destination: "spark-large"
      }
    ]);
  });

  it("preserves unknown keys when editing an existing preset", () => {
    const existing: PresetRead = {
      id: "00000000-0000-0000-0000-000000000010",
      name: "custom",
      destination_id: destinationId,
      mode: "standard",
      operations: ["optimize"],
      params: {
        min_small_files: 10,
        partition_col: "event_date",
        custom_flag: true,
        chunk_size_gb: 1
      },
      guardrails: {
        cooldown_hours: 6,
        max_table_gb: 500
      },
      priority: 100,
      enabled: true,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z"
    };

    const body = formValuesToPresetWrite(
      {
        ...baseFormValues(),
        min_small_files: 20,
        cooldown_hours: 8
      },
      existing
    );

    expect(body.params.partition_col).toBe("event_date");
    expect(body.params.custom_flag).toBe(true);
    expect(body.params.min_small_files).toBe(20);
    expect(body.params.chunk_size_gb).toBe(100);
    expect(body.guardrails.max_table_gb).toBe(500);
    expect(body.guardrails.cooldown_hours).toBe(8);
  });

  it("omits cleared optional fields", () => {
    const existing: PresetRead = {
      id: "00000000-0000-0000-0000-000000000010",
      name: "custom",
      destination_id: destinationId,
      mode: "standard",
      operations: ["optimize"],
      params: { min_small_files: 10, max_chunks: 2, stats_columns: ["id"] },
      guardrails: { cooldown_hours: 6, engine_pool: ["trino-main"] },
      priority: 100,
      enabled: true,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z"
    };

    const body = formValuesToPresetWrite(
      {
        ...baseFormValues(),
        min_small_files: 10,
        max_chunks: null,
        stats_columns: [],
        engine_pool: []
      },
      existing
    );

    expect(body.params.max_chunks).toBeUndefined();
    expect(body.params.stats_columns).toBeUndefined();
    expect(body.guardrails.engine_pool).toBeUndefined();
  });
});

describe("formatPresetSummary", () => {
  it("renders a compact preset summary", () => {
    const summary = formatPresetSummary({
      id: "00000000-0000-0000-0000-000000000010",
      name: "standard",
      destination_id: destinationId,
      mode: "standard",
      operations: ["optimize"],
      params: { min_small_files: 64, retention_days: 5, retain_last: 2 },
      guardrails: { cooldown_hours: 12, adaptive_routing: true, engine_preferences: { spark_min_gb: 120 } },
      priority: 100,
      enabled: true,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z"
    });

    expect(summary).toContain("64 small files");
    expect(summary).toContain("100 GB chunks");
    expect(summary).toContain("expire 5d");
    expect(summary).toContain("12h cooldown");
    expect(summary).toContain("adaptive routing");
  });
});
