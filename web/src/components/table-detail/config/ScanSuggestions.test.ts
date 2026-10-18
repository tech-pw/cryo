import { describe, expect, it } from "vitest";
import type { PropertyRecommendationRead } from "../../../api/client";
import type { TableSettingRead } from "../../../api/types";
import { stagingFor, visibleSuggestions } from "./ScanSuggestions";

const setting = (key: string, overrides: Partial<TableSettingRead> = {}) =>
  ({ key, current_value: null, recommended_value: null, editable: true, ...overrides }) as TableSettingRead;

const item = (overrides: Partial<PropertyRecommendationRead>): PropertyRecommendationRead => ({
  property_key: "commit.retry.num-retries",
  current_value: null,
  recommended_value: null,
  layout_recommendation_id: null,
  bloom_filter_column: null,
  reason: "Frequent commit conflicts",
  tier: "tier1",
  action: "apply",
  needs_confirmation: false,
  ...overrides
});

describe("ScanSuggestions", () => {
  const settings = new Map([
    ["write.target-file-size-bytes", setting("write.target-file-size-bytes", { recommended_value: "536870912" })],
    ["commit.retry.num-retries", setting("commit.retry.num-retries", { current_value: "4" })],
    ["write.parquet.bloom-filter-columns", setting("write.parquet.bloom-filter-columns", { current_value: "user_id" })]
  ]);

  it("drops suggestions an inventory row already answers, so a property never gets two recommendations", () => {
    const visible = visibleSuggestions(
      [
        item({ property_key: "write.target-file-size-bytes", recommended_value: "268435456" }),
        item({ recommended_value: "10" }),
        item({ property_key: "gc.enabled", action: "banner", tier: "tier3" })
      ],
      settings
    );
    expect(visible.map((entry) => entry.property_key)).toEqual(["commit.retry.num-retries"]);
  });

  it("stages a property on its row and a bloom column onto the existing column list", () => {
    expect(stagingFor(item({ recommended_value: "10" }), settings)).toEqual({
      setting: settings.get("commit.retry.num-retries"),
      value: "10"
    });
    const bloom = item({
      property_key: "write.parquet.bloom-filter-enabled.column.session_id",
      action: "link_bloom",
      bloom_filter_column: "session_id"
    });
    expect(stagingFor(bloom, settings)?.value).toBe("user_id,session_id");
    expect(stagingFor({ ...bloom, bloom_filter_column: "user_id" }, settings)).toBeNull();
  });
});
