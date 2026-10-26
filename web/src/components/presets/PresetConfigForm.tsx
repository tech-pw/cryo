import { useMutation, useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  createPreset,
  listDestinations,
  patchPreset,
  listPresetRules,
  createPresetRule,
  deletePresetRule,
  simulatePreset
} from "../../api/client";
import type { DestinationRead, PresetRead, PresetWrite, UUID } from "../../api/types";
import { ConfirmAction } from "../confirm";
import { CronInput } from "../CronInput";
import { PresetConfigFields } from "./PresetConfigFields";
import { FilterBar } from "../FilterBar";
import { Alert } from "../ui/alert";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { FormField, FormProvider, useForm } from "../ui/form";
import { Input } from "../ui/input";
import { InputNumber } from "../ui/input-number";
import { Label } from "../ui/label";
import { Select } from "../ui/select";
import { Switch } from "../ui/switch";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle
} from "../ui/alert-dialog";
import { DataTable } from "../ui/data-table";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../ui/tooltip";
import { useAuth } from "../../lib/auth";
import {
  formatDestinationLabel,
  isNativeDestination,
  nativeRustOptimizeTooltip,
  nativeSupportsOperation,
  nativeUnsupportedOpTooltip,
  resolveNativeEngineImpl
} from "../../lib/engines";
import {
  formValuesToPresetWrite,
  presetLifecycleWarning,
  presetToFormValues,
  validatePresetFormValues,
  type PresetFormValues
} from "../../lib/presetForm";
import { validateOperationOrder } from "../../lib/operationOrder";
import { tableFiltersToPredicate, type TableFilterState } from "../../lib/predicate";

// Selectable ops, listed in the canonical execution order (see ../../lib/operationOrder.ts).
const operations = ["optimize", "optimize_manifests", "expire_snapshots", "remove_orphan_files"];

export const PresetConfigForm = ({
  preset,
  loading,
  onSaved
}: {
  preset?: PresetRead;
  loading?: boolean;
  onSaved: () => void;
}) => {
  const { api, role } = useAuth();
  const destinations = useQuery({ queryKey: ["destinations"], queryFn: () => listDestinations(api) });

  const form = useForm<PresetFormValues>({
    defaultValues: presetToFormValues(null, destinations.data ?? []) as PresetFormValues
  });

  const destinationId = form.watch("destination_id");
  const selectedOps = form.watch("operations");
  const operationOrderWarnings = useMemo(() => validateOperationOrder(selectedOps ?? []), [selectedOps]);
  const selectedDestination = useMemo(
    () => (destinations.data ?? []).find((item) => item.id === destinationId),
    [destinations.data, destinationId]
  );
  const isNative = isNativeDestination(selectedDestination);
  const nativeImpl = selectedDestination ? resolveNativeEngineImpl(selectedDestination.config) : undefined;

  useEffect(() => {
    if (preset && destinations.data) {
      form.reset(presetToFormValues(preset, destinations.data) as PresetFormValues);
    }
  }, [preset, destinations.data, form]);

  useEffect(() => {
    const id = form.getValues("destination_id");
    const destination = (destinations.data ?? []).find((item) => item.id === id);
    if (!destination || !isNativeDestination(destination)) return;
    const impl = resolveNativeEngineImpl(destination.config);
    const currentOps = form.getValues("operations");
    const nextOps = (currentOps ?? []).filter((op) => nativeSupportsOperation(op, impl ?? "go"));
    if (nextOps.length !== (currentOps ?? []).length) {
      form.setValue("operations", nextOps);
    }
    if (form.getValues("mode") === "manifest_only") {
      form.setValue("mode", "deep");
    }
  }, [destinationId, destinations.data, form]);

  const save = useMutation({
    mutationFn: (body: PresetWrite) =>
      preset ? patchPreset(api, preset.id, body) : createPreset(api, body),
    onSuccess: () => {
      toast.success("Preset saved");
      onSaved();
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const operationOptions = operations.map((operation) => {
    const supported = !isNative || nativeSupportsOperation(operation, nativeImpl ?? "go");
    const tooltip =
      operation === "optimize_manifests"
        ? nativeUnsupportedOpTooltip
        : operation === "optimize"
          ? nativeRustOptimizeTooltip
          : undefined;
    return { operation, supported, tooltip };
  });

  const modeOptions = [
    { label: "Light", value: "light", disabled: false },
    { label: "Standard", value: "standard", disabled: false },
    { label: "Deep", value: "deep", disabled: false },
    {
      label: "Manifest only",
      value: "manifest_only",
      disabled: isNative,
      tooltip: isNative ? nativeUnsupportedOpTooltip : undefined
    }
  ];

  const [pendingSave, setPendingSave] = useState<{ body: PresetWrite; message: string } | null>(null);

  const submit = async (values: PresetFormValues) => {
    const errors = validatePresetFormValues(values);
    if (errors) {
      for (const [key, message] of Object.entries(errors)) {
        form.setError(key as keyof PresetFormValues, { message });
      }
      return;
    }
    const body = formValuesToPresetWrite(values, preset);
    if (preset && presetLifecycleWarning(preset, values, null)) {
      let covered: number | null = null;
      try {
        covered = (await simulatePreset(api, preset.id)).matched_count;
      } catch {
        covered = null;
      }
      setPendingSave({ body, message: presetLifecycleWarning(preset, values, covered) ?? "" });
      return;
    }
    save.mutate(body);
  };

  if (loading) return null;

  const disabled = role !== "admin";

  return (
    <div className="flex w-full flex-col gap-4">
      <FormProvider {...form}>
        <form className="flex flex-col gap-4" onSubmit={form.handleSubmit(submit)}>
          <FormField name="name" label="Name">
            {(field) => (
              <Input
                disabled={disabled}
                value={String(field.value ?? "")}
                onChange={field.onChange}
                onBlur={field.onBlur}
                name={field.name}
                ref={field.ref}
              />
            )}
          </FormField>
          <FormField name="description" label="Description">
            {(field) => (
              <Input
                disabled={disabled}
                value={String(field.value ?? "")}
                onChange={field.onChange}
                onBlur={field.onBlur}
                name={field.name}
                ref={field.ref}
              />
            )}
          </FormField>
          <FormField name="destination_id" label="Engine">
            {(field) => (
              <Select
                disabled={disabled}
                value={String(field.value ?? "")}
                onChange={(next) => field.onChange(next ?? "")}
                options={(destinations.data ?? []).map((item: DestinationRead) => ({
                  label: formatDestinationLabel(item),
                  value: item.id
                }))}
                onBlur={field.onBlur}
                name={field.name}
                ref={field.ref}
              />
            )}
          </FormField>

          <div className="flex flex-wrap gap-3">
            <FormField name="mode" label="Mode" className="w-40">
              {(field) => (
                <Select
                  disabled={disabled}
                  value={String(field.value ?? "")}
                  onChange={(next) => field.onChange(next ?? "")}
                  options={modeOptions.map((opt) => ({
                    label: opt.label,
                    value: opt.value,
                    disabled: opt.disabled
                  }))}
                  onBlur={field.onBlur}
                  name={field.name}
                  ref={field.ref}
                />
              )}
            </FormField>
            <FormField name="priority" label="Priority" className="w-32">
              {(field) => (
                <InputNumber
                  disabled={disabled}
                  min={0}
                  value={field.value as number | null | undefined}
                  onChange={field.onChange}
                  onBlur={field.onBlur}
                  name={field.name}
                  ref={field.ref}
                />
              )}
            </FormField>
            <div className="flex items-center gap-2 pt-6">
              <Switch
                id="preset-enabled"
                disabled={disabled}
                checked={form.watch("enabled")}
                onCheckedChange={(checked) => form.setValue("enabled", checked)}
              />
              <Label htmlFor="preset-enabled">Enabled</Label>
            </div>
            <div className="flex items-center gap-2 pt-6">
              <Switch
                id="preset-paused"
                disabled={disabled}
                checked={form.watch("paused")}
                onCheckedChange={(checked) => form.setValue("paused", checked)}
              />
              <Label htmlFor="preset-paused">Paused</Label>
            </div>
          </div>
          <p className="-mt-2 text-xs text-muted-foreground">
            Paused holds this preset&apos;s tables: they keep it and wait, nothing runs. Turning
            Enabled off retires the preset, and its pinned tables fall through to rules or the default.
          </p>

          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Switch
                id="preset-default"
                disabled={disabled}
                checked={form.watch("is_default")}
                onCheckedChange={(checked) => form.setValue("is_default", checked)}
              />
              <Label htmlFor="preset-default">Default preset</Label>
            </div>
            <p className="text-xs text-muted-foreground">
              Runs for every table that has no pinned preset and matches no rule, including newly
              discovered tables. Needs a schedule. Only one preset can be the default.
            </p>
          </div>

          <div className="space-y-2">
            <Label>Operations</Label>
            <div className="flex flex-wrap gap-3">
              {operationOptions.map(({ operation, supported, tooltip }) => (
                <label key={operation} className="flex items-center gap-2 text-sm">
                  {tooltip ? (
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="flex items-center gap-2">
                            <Checkbox
                              disabled={disabled || !supported}
                              checked={(selectedOps ?? []).includes(operation)}
                              onCheckedChange={(checked) => {
                                const current = form.getValues("operations") ?? [];
                                form.setValue(
                                  "operations",
                                  checked
                                    ? [...current, operation]
                                    : current.filter((op) => op !== operation)
                                );
                              }}
                            />
                            <span>{operation}</span>
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>{tooltip}</TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  ) : (
                    <>
                      <Checkbox
                        disabled={disabled || !supported}
                        checked={(selectedOps ?? []).includes(operation)}
                        onCheckedChange={(checked) => {
                          const current = form.getValues("operations") ?? [];
                          form.setValue(
                            "operations",
                            checked ? [...current, operation] : current.filter((op) => op !== operation)
                          );
                        }}
                      />
                      <span>{operation}</span>
                    </>
                  )}
                </label>
              ))}
            </div>
          </div>

          {operationOrderWarnings.length > 0 ? (
            <Alert
              variant="warning"
              title="Operations are out of the recommended order"
              description={
                <ul className="m-0 list-inside list-disc pl-0">
                  {operationOrderWarnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              }
            />
          ) : null}

          <div className="space-y-1.5">
            <Label htmlFor="schedule_cron">Schedule cron</Label>
            <CronInput
              allowEmpty
              value={form.watch("schedule_cron") ?? ""}
              onChange={(cron) => form.setValue("schedule_cron", cron)}
            />
            {form.formState.errors.schedule_cron?.message ? (
              <p className="text-xs text-destructive" role="alert">{form.formState.errors.schedule_cron.message}</p>
            ) : null}
          </div>

          <PresetConfigFields destinations={destinations.data ?? []} />

          <Button type="submit" loading={save.isPending} disabled={disabled}>
            Save preset
          </Button>
        </form>
      </FormProvider>
      {preset ? <PresetRulesSection presetId={preset.id} /> : null}
      <AlertDialog open={pendingSave !== null} onOpenChange={(open) => (open ? null : setPendingSave(null))}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Change who maintains these tables?</AlertDialogTitle>
            <AlertDialogDescription>{pendingSave?.message}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setPendingSave(null)}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (pendingSave) save.mutate(pendingSave.body);
                setPendingSave(null);
              }}
            >
              Save preset
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};

const PresetRulesSection = ({ presetId }: { presetId: UUID }) => {
  const { api, role } = useAuth();
  const [filters, setFilters] = useState<TableFilterState>({ activeOnly: true });
  const rules = useQuery({
    queryKey: ["preset-rules", presetId],
    queryFn: () => listPresetRules(api, presetId)
  });
  const addRule = useMutation({
    mutationFn: () => {
      const predicate = tableFiltersToPredicate(filters);
      if (!predicate) throw new Error("Choose at least one filter.");
      const onlyActive =
        "field" in predicate && predicate.field === "is_active" && predicate.op === "=" && predicate.value === true;
      if (onlyActive) {
        throw new Error("Choose at least one filter. Active tables alone matches the whole fleet.");
      }
      return createPresetRule(api, presetId, { predicate, enabled: true });
    },
    onSuccess: () => {
      toast.success("Rule added");
      void rules.refetch();
    },
    onError: (error) => toast.error((error as Error).message)
  });
  const removeRule = useMutation({
    mutationFn: (ruleId: UUID) => deletePresetRule(api, presetId, ruleId),
    onSuccess: () => {
      toast.success("Rule deleted");
      void rules.refetch();
    },
    onError: (error) => toast.error((error as Error).message)
  });

  type PresetRuleRow = { id: UUID; enabled: boolean; predicate: unknown };
  const columns: ColumnDef<PresetRuleRow, unknown>[] = [
    { id: "enabled", header: "Enabled", cell: ({ row }) => String(row.original.enabled) },
    {
      id: "predicate",
      header: "Predicate",
      cell: ({ row }) => <pre className="inline-json">{JSON.stringify(row.original.predicate)}</pre>
    },
    ...(role === "admin"
      ? [
          {
            id: "actions",
            header: "Actions",
            cell: ({ row }: { row: { original: PresetRuleRow } }) => (
              <ConfirmAction title="Delete this rule?" onConfirm={() => removeRule.mutate(row.original.id)}>
                <Button size="sm" variant="danger" loading={removeRule.isPending}>
                  Delete
                </Button>
              </ConfirmAction>
            )
          } as ColumnDef<PresetRuleRow, unknown>
        ]
      : [])
  ];

  return (
    <div className="space-y-3">
      <h4 className="text-sm font-semibold">Assignment rules</h4>
      <FilterBar value={filters} onChange={setFilters} />
      <Button loading={addRule.isPending} disabled={role !== "admin"} onClick={() => addRule.mutate()}>
        Add rule
      </Button>
      <DataTable
        columns={columns}
        data={(rules.data ?? []) as PresetRuleRow[]}
        density="compact"
        loading={rules.isLoading}
        getRowId={(row) => row.id}
        emptyTitle="No assignment rules"
      />
    </div>
  );
};
