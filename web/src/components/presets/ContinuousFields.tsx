import { useFormContext } from "react-hook-form";
import { HintLabel } from "../common";
import type { PresetFormValues } from "../../lib/presetForm";
import { Collapsible } from "../ui/collapsible";
import { FormField } from "../ui/form";
import { InputNumber } from "../ui/input-number";
import { Label } from "../ui/label";
import { Separator } from "../ui/separator";
import { Switch } from "../ui/switch";

type NumberField = {
  name: keyof PresetFormValues;
  label: string;
  tooltip: string;
  min: number;
  max?: number;
  placeholder?: string;
};

const THRESHOLDS: NumberField[] = [
  {
    name: "continuous_max_commits",
    label: "Writer commits",
    tooltip: "Maintain once this many writer commits have landed since the last successful run.",
    min: 1,
    placeholder: "Off"
  },
  {
    name: "continuous_max_small_files_added",
    label: "Small files added",
    tooltip: "Maintain once writers have added this many files below the small-file size.",
    min: 1,
    placeholder: "Off"
  },
  {
    name: "continuous_max_delete_file_pct",
    label: "Delete-file share (%)",
    tooltip: "Maintain once delete files make up this share of the table's files.",
    min: 0,
    max: 100,
    placeholder: "Off"
  },
  {
    name: "continuous_max_score_drop",
    label: "Health score drop",
    tooltip: "Maintain once the estimated health score has dropped this many points since the last run.",
    min: 1,
    max: 100,
    placeholder: "Off"
  }
];

const PACING: NumberField[] = [
  {
    name: "continuous_debounce_minutes",
    label: "Quiet period (min)",
    tooltip: "Wait until writers have been quiet this long, so a burst of commits becomes one run.",
    min: 0
  },
  {
    name: "continuous_max_delay_minutes",
    label: "Max delay (min)",
    tooltip: "Stop waiting for quiet this long after the first unmaintained commit.",
    min: 1
  },
  {
    name: "continuous_min_interval_minutes",
    label: "Min interval (min)",
    tooltip: "Never start a continuous run sooner than this after the table was last maintained.",
    min: 1
  },
  {
    name: "continuous_max_runs_per_hour",
    label: "Runs per hour",
    tooltip: "Per-table cap on continuous runs in any hour. Empty means no cap.",
    min: 1,
    placeholder: "No cap"
  },
  {
    name: "continuous_max_runs_per_day",
    label: "Runs per day",
    tooltip: "Per-table cap on continuous runs in any 24 hours. Empty means no cap.",
    min: 1,
    placeholder: "No cap"
  },
  {
    name: "continuous_max_maintenance_commits_per_day",
    label: "Maintenance commits per day",
    tooltip:
      "Snapshot-churn guard: stop triggering once maintenance itself has committed this many snapshots to the table in 24 hours.",
    min: 1,
    placeholder: "No cap"
  }
];

const NumberFields = ({ fields, disabled }: { fields: NumberField[]; disabled: boolean }) => (
  <div className="flex flex-wrap gap-3">
    {fields.map((item) => (
      <FormField key={item.name} name={item.name} label={<HintLabel label={item.label} tooltip={item.tooltip} />}>
        {(field) => (
          <InputNumber
            min={item.min}
            max={item.max}
            placeholder={item.placeholder}
            disabled={disabled}
            className="w-44"
            value={field.value as number | null | undefined}
            onChange={field.onChange}
            onBlur={field.onBlur}
            name={field.name}
            ref={field.ref}
          />
        )}
      </FormField>
    ))}
  </div>
);

export const ContinuousFields = ({ disabled }: { disabled: boolean }) => {
  const form = useFormContext<PresetFormValues>();
  const enabled = Boolean(form.watch("continuous_enabled"));
  const error = form.formState.errors.continuous_enabled?.message;

  return (
    <>
      <Separator className="my-4" />
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Continuous maintenance</p>
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <Switch
            id="preset-continuous"
            disabled={disabled}
            checked={enabled}
            onCheckedChange={(checked) => form.setValue("continuous_enabled", checked)}
          />
          <Label htmlFor="preset-continuous">Maintain when tables drift</Label>
        </div>
        <p className="text-xs text-muted-foreground">
          Also runs a table as soon as writers push it past a threshold, instead of only on the schedule. Jobs still
          pass every governor gate: cooldown, off-peak windows, engine load and the per-table lock.
        </p>
        {error ? (
          <p className="text-xs text-destructive" role="alert">
            {error}
          </p>
        ) : null}
      </div>
      {enabled ? (
        <>
          <p className="text-xs text-muted-foreground">Trigger when any of these is reached.</p>
          <NumberFields fields={THRESHOLDS} disabled={disabled} />
          <Collapsible label="Pacing and churn limits">
            <NumberFields fields={PACING} disabled={disabled} />
          </Collapsible>
        </>
      ) : null}
    </>
  );
};
