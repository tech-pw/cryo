import { describe, expect, it } from "vitest";
import { DEFAULT_PRESET_NEEDS_SCHEDULE, presetLifecycleWarning, validatePresetFormValues } from "./presetForm";
import type { PresetFormValues } from "./presetForm";

const live = { enabled: true, paused: false, is_default: true };

describe("presetLifecycleWarning", () => {
  it("is silent for a new preset or an unchanged one", () => {
    expect(presetLifecycleWarning(null, live, 3)).toBeNull();
    expect(presetLifecycleWarning(live, live, 3)).toBeNull();
  });

  it("warns how many tables stop being maintained when the default is paused", () => {
    expect(presetLifecycleWarning(live, { ...live, paused: true }, 12)).toBe(
      "Pausing holds this preset. 12 tables will stop being maintained until you resume it."
    );
  });

  it("warns when the default is unset", () => {
    expect(presetLifecycleWarning(live, { ...live, is_default: false }, 1)).toBe(
      "1 table on the default will stop being maintained unless another preset is made the default."
    );
  });

  it("explains that disabling retires the preset and its tables fall through", () => {
    expect(presetLifecycleWarning(live, { ...live, enabled: false }, 4)).toMatch(/fall through/);
  });

  it("still warns when the count is unavailable", () => {
    expect(presetLifecycleWarning(live, { ...live, paused: true }, null)).toMatch(/resume it/);
  });
});

describe("validatePresetFormValues default schedule", () => {
  const values = {
    name: "default",
    destination_id: "d",
    mode: "standard",
    operations: ["optimize"],
    priority: 0,
    enabled: true,
    paused: false,
    is_default: true,
    schedule_cron: ""
  } as unknown as PresetFormValues;

  it("requires a schedule on the default preset", () => {
    expect(validatePresetFormValues(values)?.schedule_cron).toBe(DEFAULT_PRESET_NEEDS_SCHEDULE);
    expect(validatePresetFormValues({ ...values, schedule_cron: "0 2 * * *" })?.schedule_cron).toBeUndefined();
  });
});
