import { useFormContext } from "react-hook-form";
import type { PresetModeCatalog } from "../../api/types";
import { formatNumber } from "../../lib/format";
import { gibToBytes, type PresetFormValues } from "../../lib/presetForm";
import { effectiveDeepParam, SOURCE_LABELS, type DeepParamKey } from "../../lib/presetModes";
import { HintLabel } from "../common";
import { FormField } from "../ui/form";
import { InputNumber } from "../ui/input-number";

type DeepField = {
  name: "orphan_cadence_days" | "orphan_catchup_min_gb" | "orphan_max_listing_keys" | "min_manifests";
  param: DeepParamKey;
  label: string;
  tooltip: string;
  format: (value: number) => string;
  toParam?: (value: number) => number;
  operation: "remove_orphan_files" | "optimize_manifests";
};

const GIB = 1024 ** 3;

const DEEP_FIELDS: DeepField[] = [
  {
    name: "orphan_cadence_days",
    param: "orphan_cadence_days",
    label: "Orphan cleanup every (days)",
    tooltip:
      "Plan remove_orphan_files on a table at most once per this many days, staggered across nights. 0 cleans on every deep run.",
    format: (value) => (value === 0 ? "every deep run" : `every ${formatNumber(value)} ${value === 1 ? "day" : "days"}`),
    operation: "remove_orphan_files"
  },
  {
    name: "orphan_catchup_min_gb",
    param: "orphan_catchup_min_bytes",
    label: "Catch-up threshold (GiB)",
    tooltip:
      "A never-cleaned table whose last listing found at least this much orphan data skips the stagger wait once. 0 disables catch-up.",
    format: (bytes) => (bytes === 0 ? "off" : `${formatNumber(Math.round((bytes / GIB) * 100) / 100)} GiB`),
    toParam: gibToBytes,
    operation: "remove_orphan_files"
  },
  {
    name: "orphan_max_listing_keys",
    param: "orphan_max_listing_keys",
    label: "Listing cap (objects)",
    tooltip:
      "Skip orphan cleanup when the table root holds more objects than this; the listing runs on one driver. 0 disables the cap.",
    format: (value) => (value === 0 ? "no cap" : `${formatNumber(value)} objects`),
    operation: "remove_orphan_files"
  },
  {
    name: "min_manifests",
    param: "min_manifests",
    label: "Manifest threshold",
    tooltip: "Rewrite manifests only when the last scan counted at least this many.",
    format: (value) => `${formatNumber(value)} manifests`,
    operation: "optimize_manifests"
  }
];

/** Thresholds behind deep mode's orphan cleanup and manifest rewrite. Empty inherits the default. */
export const DeepModeFields = ({
  catalog,
  operations,
  disabled
}: {
  catalog: PresetModeCatalog | undefined;
  operations: string[] | null;
  disabled?: boolean;
}) => {
  const form = useFormContext<PresetFormValues>();
  const visible = DEEP_FIELDS.filter((field) => operations?.includes(field.operation));
  if (visible.length === 0) return null;

  return (
    <section className="space-y-2" aria-labelledby="deep-mode-heading">
      <p id="deep-mode-heading" className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Orphan cleanup and manifests
      </p>
      <p className="text-xs text-muted-foreground">
        Leave a field empty to use the default shown under it.
      </p>
      <div className="flex flex-wrap gap-4">
        {visible.map((field) => {
          const raw = form.watch(field.name);
          const presetValue = raw == null ? null : field.toParam ? field.toParam(raw) : raw;
          const effective = effectiveDeepParam(catalog, field.param, presetValue);
          return (
            <div key={field.name} className="w-56 space-y-1">
              <FormField name={field.name} label={<HintLabel label={field.label} tooltip={field.tooltip} />}>
                {(control) => (
                  <InputNumber
                    disabled={disabled}
                    min={0}
                    className="w-full"
                    placeholder="Default"
                    value={control.value as number | null | undefined}
                    onChange={control.onChange}
                    onBlur={control.onBlur}
                    name={control.name}
                    ref={control.ref}
                  />
                )}
              </FormField>
              <p className="text-xs text-muted-foreground" data-testid={`effective-${field.name}`}>
                {effective.value == null
                  ? "Default unavailable"
                  : `Effective: ${field.format(effective.value)} · ${SOURCE_LABELS[effective.source]}`}
              </p>
            </div>
          );
        })}
      </div>
    </section>
  );
};
