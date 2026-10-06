import { MinusCircle, Plus } from "lucide-react";
import { useFieldArray, useFormContext } from "react-hook-form";
import type { DestinationRead } from "../../api/types";
import { HintLabel } from "../common";
import type { PresetFormValues } from "../../lib/presetForm";
import { ROUTING_RULE_FIELDS, ROUTING_RULE_OPERATORS } from "../../lib/presetOptions";
import { Button } from "../ui/button";
import { Collapsible } from "../ui/collapsible";
import { FormField } from "../ui/form";
import { InputNumber } from "../ui/input-number";
import { Label } from "../ui/label";
import { MultiSelect, Select } from "../ui/select";
import { Separator } from "../ui/separator";
import { Switch } from "../ui/switch";
import { TagsInput } from "../ui/tags-input";

export const PresetConfigFields = ({ destinations }: { destinations: DestinationRead[] }) => {
  const form = useFormContext<PresetFormValues>();
  const { fields, append, remove } = useFieldArray({ control: form.control, name: "routing_rules" });

  const destinationOptions = destinations.map((item) => ({
    label: item.name,
    value: item.name
  }));

  return (
    <>
      <Separator className="my-4" />
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Optimization planning</p>
      <p className="text-xs text-muted-foreground">
        Controls when Cryo plans optimize work and how large each chunk can be. Native Kubernetes Jobs ignore the
        stored chunk size and derive it from pod memory.
      </p>
      <div className="flex flex-wrap gap-3">
        <FormField
          name="min_small_files"
          label={
            <HintLabel
              label="Min small files"
              tooltip="Skip optimize unless a table or partition has at least this many small files."
            />
          }
        >
          {(field) => (
            <InputNumber
              min={0}
              className="w-40"
              value={field.value as number | null | undefined}
              onChange={field.onChange}
              onBlur={field.onBlur}
              name={field.name}
              ref={field.ref}
            />
          )}
        </FormField>
        <FormField
          name="chunk_size_gb"
          label={
            <HintLabel
              label="Chunk size (GB)"
              tooltip="Maximum data in one optimize chunk for Trino and Spark."
            />
          }
        >
          {(field) => (
            <InputNumber
              min={1}
              className="w-40"
              value={field.value as number | null | undefined}
              onChange={field.onChange}
              onBlur={field.onBlur}
              name={field.name}
              ref={field.ref}
            />
          )}
        </FormField>
        <FormField
          name="max_partitions_per_chunk"
          label={
            <HintLabel
              label="Max partitions / chunk"
              tooltip="Caps partitions per optimize statement to avoid Trino FilterNode complexity errors."
            />
          }
        >
          {(field) => (
            <InputNumber
              min={1}
              className="w-44"
              value={field.value as number | null | undefined}
              onChange={field.onChange}
              onBlur={field.onBlur}
              name={field.name}
              ref={field.ref}
            />
          )}
        </FormField>
        <FormField
          name="max_chunks"
          label={<HintLabel label="Max chunks" tooltip="Optional cap on optimize chunks generated for one table." />}
        >
          {(field) => (
            <InputNumber
              min={1}
              placeholder="No cap"
              className="w-36"
              value={field.value as number | null | undefined}
              onChange={field.onChange}
              onBlur={field.onBlur}
              name={field.name}
              ref={field.ref}
            />
          )}
        </FormField>
      </div>
      <div className="flex flex-wrap gap-3">
        <FormField
          name="delete_ratio_trigger"
          label={
            <HintLabel
              label="Delete ratio trigger (%)"
              tooltip="When delete-file percentage reaches this threshold, optimize can run even below the small-file threshold."
            />
          }
        >
          {(field) => (
            <InputNumber
              min={0}
              max={100}
              className="w-44"
              value={field.value as number | null | undefined}
              onChange={field.onChange}
              onBlur={field.onBlur}
              name={field.name}
              ref={field.ref}
            />
          )}
        </FormField>
        <FormField
          name="partition_write_idle_hours"
          label={<HintLabel label="Partition idle hours" tooltip="Skip partitions written within this many hours." />}
        >
          {(field) => (
            <InputNumber
              min={0}
              className="w-44"
              value={field.value as number | null | undefined}
              onChange={field.onChange}
              onBlur={field.onBlur}
              name={field.name}
              ref={field.ref}
            />
          )}
        </FormField>
        <FormField
          name="file_size_mb"
          label={
            <HintLabel label="Target file size (MB)" tooltip="Target output file size passed to engine optimize statements." />
          }
        >
          {(field) => (
            <InputNumber
              min={1}
              className="w-44"
              value={field.value as number | null | undefined}
              onChange={field.onChange}
              onBlur={field.onBlur}
              name={field.name}
              ref={field.ref}
            />
          )}
        </FormField>
      </div>

      <Separator className="my-4" />
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Snapshot and orphan retention</p>
      <div className="flex flex-wrap gap-3">
        <FormField name="retention_days" label={<HintLabel label="Snapshot retention (days)" />}>
          {(field) => (
            <InputNumber
              min={1}
              className="w-44"
              value={field.value as number | null | undefined}
              onChange={field.onChange}
              onBlur={field.onBlur}
              name={field.name}
              ref={field.ref}
            />
          )}
        </FormField>
        <FormField name="retain_last" label={<HintLabel label="Retain last snapshots" />}>
          {(field) => (
            <InputNumber
              min={1}
              className="w-44"
              value={field.value as number | null | undefined}
              onChange={field.onChange}
              onBlur={field.onBlur}
              name={field.name}
              ref={field.ref}
            />
          )}
        </FormField>
        <FormField name="orphan_retention_days" label={<HintLabel label="Orphan retention (days)" />}>
          {(field) => (
            <InputNumber
              min={1}
              placeholder="Use snapshot retention"
              className="w-52"
              value={field.value as number | null | undefined}
              onChange={field.onChange}
              onBlur={field.onBlur}
              name={field.name}
              ref={field.ref}
            />
          )}
        </FormField>
      </div>

      <Separator className="my-4" />
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Statistics</p>
      <div className="flex items-center gap-2">
        <Switch
          id="compute_statistics"
          checked={Boolean(form.watch("compute_statistics"))}
          onCheckedChange={(checked) => form.setValue("compute_statistics", checked)}
        />
        <Label htmlFor="compute_statistics">
          <HintLabel label="Compute statistics" />
        </Label>
      </div>
      <div className="flex flex-wrap gap-3">
        <FormField name="stats_columns" label={<HintLabel label="Stats columns" />} className="min-w-[280px] flex-1">
          {(field) => (
            <TagsInput
              placeholder="column_a, column_b"
              tokenSeparators={[","]}
              value={(field.value as string[] | undefined) ?? []}
              onChange={field.onChange}
            />
          )}
        </FormField>
        <FormField
          name="bloom_filter_columns"
          label={<HintLabel label="Bloom filter columns" />}
          className="min-w-[280px] flex-1"
        >
          {(field) => (
            <TagsInput
              placeholder="column_a, column_b"
              tokenSeparators={[","]}
              value={(field.value as string[] | undefined) ?? []}
              onChange={field.onChange}
            />
          )}
        </FormField>
      </div>
      <div className="flex items-center gap-2">
        <Switch
          id="stats_compute_ndv"
          checked={Boolean(form.watch("stats_compute_ndv"))}
          onCheckedChange={(checked) => form.setValue("stats_compute_ndv", checked)}
        />
        <Label htmlFor="stats_compute_ndv">
          <HintLabel label="Compute NDV" />
        </Label>
      </div>

      <Separator className="my-4" />
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Scheduling guardrails</p>
      <div className="flex flex-wrap gap-3">
        <FormField name="cooldown_hours" label={<HintLabel label="Cooldown (hours)" />}>
          {(field) => (
            <InputNumber
              min={0}
              className="w-40"
              value={field.value as number | null | undefined}
              onChange={field.onChange}
              onBlur={field.onBlur}
              name={field.name}
              ref={field.ref}
            />
          )}
        </FormField>
        <FormField name="deadline_minutes" label={<HintLabel label="Run deadline (minutes)" />}>
          {(field) => (
            <InputNumber
              min={1}
              placeholder="No deadline"
              className="w-44"
              value={field.value as number | null | undefined}
              onChange={field.onChange}
              onBlur={field.onBlur}
              name={field.name}
              ref={field.ref}
            />
          )}
        </FormField>
      </div>

      <Separator className="my-4" />
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">SLA thresholds</p>
      <div className="flex flex-wrap gap-3">
        <FormField name="sla_freshness_hours" label={<HintLabel label="Freshness SLA (hours)" />}>
          {(field) => (
            <InputNumber
              min={0}
              className="w-44"
              value={field.value as number | null | undefined}
              onChange={field.onChange}
              onBlur={field.onBlur}
              name={field.name}
              ref={field.ref}
            />
          )}
        </FormField>
        <FormField name="sla_maintenance_days" label={<HintLabel label="Maintenance SLA (days)" />}>
          {(field) => (
            <InputNumber
              min={0}
              className="w-44"
              value={field.value as number | null | undefined}
              onChange={field.onChange}
              onBlur={field.onBlur}
              name={field.name}
              ref={field.ref}
            />
          )}
        </FormField>
        <FormField name="sla_p95_latency_ms" label={<HintLabel label="P95 latency SLA (ms)" />}>
          {(field) => (
            <InputNumber
              min={0}
              className="w-44"
              value={field.value as number | null | undefined}
              onChange={field.onChange}
              onBlur={field.onBlur}
              name={field.name}
              ref={field.ref}
            />
          )}
        </FormField>
      </div>

      <Separator className="my-4" />
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Adaptive routing</p>
      <div className="flex items-center gap-2">
        <Switch
          id="adaptive_routing"
          checked={Boolean(form.watch("adaptive_routing"))}
          onCheckedChange={(checked) => form.setValue("adaptive_routing", checked)}
        />
        <Label htmlFor="adaptive_routing">
          <HintLabel label="Adaptive routing" />
        </Label>
      </div>
      <FormField name="engine_pool" label={<HintLabel label="Engine pool" />}>
        {(field) => (
          <MultiSelect
            className="w-full"
            placeholder="All destinations"
            options={destinationOptions}
            value={(field.value as string[] | undefined) ?? []}
            onChange={field.onChange}
          />
        )}
      </FormField>
      <div className="flex flex-wrap gap-3">
        <FormField name="spark_min_gb" label={<HintLabel label="Prefer Spark above (GB)" />}>
          {(field) => (
            <InputNumber
              min={0}
              className="w-44"
              value={field.value as number | null | undefined}
              onChange={field.onChange}
              onBlur={field.onBlur}
              name={field.name}
              ref={field.ref}
            />
          )}
        </FormField>
        <FormField name="native_min_gb" label={<HintLabel label="Prefer native above (GB)" />}>
          {(field) => (
            <InputNumber
              min={0}
              className="w-44"
              value={field.value as number | null | undefined}
              onChange={field.onChange}
              onBlur={field.onBlur}
              name={field.name}
              ref={field.ref}
            />
          )}
        </FormField>
        <FormField name="latency_p99_ms" label={<HintLabel label="Catalog latency threshold (ms)" />}>
          {(field) => (
            <InputNumber
              min={0}
              className="w-52"
              value={field.value as number | null | undefined}
              onChange={field.onChange}
              onBlur={field.onBlur}
              name={field.name}
              ref={field.ref}
            />
          )}
        </FormField>
      </div>

      <Collapsible label="Routing rules (optional)">
        <p className="mb-3 text-xs text-muted-foreground">
          Route tables to a different destination when a predicate matches. Rules are evaluated in order.
        </p>
        {fields.map((field, index) => (
          <div key={field.id} className="mb-2 flex flex-wrap items-start gap-2">
            <FormField name={`routing_rules.${index}.field`} label="Field" className="w-44">
              {(formField) => (
                <Select
                  placeholder="Field"
                  options={[...ROUTING_RULE_FIELDS]}
                  value={String(formField.value ?? "")}
                  onChange={(next) => formField.onChange(next ?? "")}
                />
              )}
            </FormField>
            <FormField name={`routing_rules.${index}.op`} label="Op" className="w-24">
              {(formField) => (
                <Select
                  placeholder="Op"
                  options={[...ROUTING_RULE_OPERATORS]}
                  value={String(formField.value ?? ">")}
                  onChange={(next) => formField.onChange(next ?? ">")}
                />
              )}
            </FormField>
            <FormField name={`routing_rules.${index}.value`} label="Value" className="w-32">
              {(formField) => (
                <InputNumber
                  placeholder="Value"
                  value={formField.value as number | null | undefined}
                  onChange={formField.onChange}
                />
              )}
            </FormField>
            <FormField name={`routing_rules.${index}.destination`} label="Destination" className="w-44">
              {(formField) => (
                <Select
                  placeholder="Destination"
                  options={destinationOptions}
                  value={String(formField.value ?? "")}
                  onChange={(next) => formField.onChange(next ?? "")}
                />
              )}
            </FormField>
            <Button type="button" variant="ghost" size="sm" aria-label="Remove routing rule" onClick={() => remove(index)}>
              <MinusCircle className="size-4" />
            </Button>
          </div>
        ))}
        {form.formState.errors.routing_rules?.message ? (
          <p className="mb-2 text-xs text-destructive" role="alert">
            {String(form.formState.errors.routing_rules.message)}
          </p>
        ) : null}
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => append({ field: "", op: ">", value: "", destination: "" })}
        >
          <Plus className="mr-1 size-4" />
          Add routing rule
        </Button>
      </Collapsible>
    </>
  );
};
