import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { createPreset, listDestinations, patchPreset, simulatePreset } from "../../api/client";
import type { DestinationRead, PresetRead, PresetSimulationResponse, PresetWrite } from "../../api/types";
import { CronInput } from "../CronInput";
import { DiscardChangesDialog } from "../DiscardChangesDialog";
import { ContinuousFields } from "./ContinuousFields";
import { DeepModeFields } from "./DeepModeFields";
import { PresetConfigFields } from "./PresetConfigFields";
import { Alert } from "../ui/alert";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { FormField, FormProvider, useForm } from "../ui/form";
import { Input } from "../ui/input";
import { InputNumber } from "../ui/input-number";
import { Label } from "../ui/label";
import { RadioGroup } from "../ui/radio-group";
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
import { useAuth } from "../../lib/auth";
import { formatDestinationLabel, isNativeDestination, nativeUnsupportedOpTooltip } from "../../lib/engines";
import { formatNumber } from "../../lib/format";
import {
  formValuesToPresetWrite,
  presetLifecycleWarning,
  presetToFormValues,
  validatePresetFormValues,
  type PresetFormValues
} from "../../lib/presetForm";
import {
  formatOrphanBacklog,
  modeLabel,
  modeOperationDiff,
  modeOperations,
  operationLabel,
  type OperationDiff
} from "../../lib/presetModes";
import { usePresetModes } from "../../lib/usePresetModes";
import { useUnsavedChangesGuard } from "../../lib/useUnsavedChangesGuard";

interface PendingSave {
  body: PresetWrite;
  lifecycle: string | null;
  modeChange: { from: string; to: string; diff: OperationDiff | null } | null;
  simulation: PresetSimulationResponse | null;
}

// Operations are derived from the mode, so they are not part of the comparison.
const snapshot = (values: PresetFormValues, preset: PresetRead | undefined): string =>
  JSON.stringify({ ...formValuesToPresetWrite(values, preset, []), operations: [] });

export const PresetConfigForm = ({
  preset,
  loading,
  onSaved,
  onDirtyChange
}: {
  preset?: PresetRead;
  loading?: boolean;
  onSaved: () => void;
  onDirtyChange?: (dirty: boolean) => void;
}) => {
  const { api, role } = useAuth();
  const queryClient = useQueryClient();
  const destinations = useQuery({ queryKey: ["destinations"], queryFn: () => listDestinations(api) });
  const modes = usePresetModes();

  const form = useForm<PresetFormValues>({
    defaultValues: presetToFormValues(null, destinations.data ?? []) as PresetFormValues
  });
  const [baseline, setBaseline] = useState<PresetFormValues | null>(null);

  const resetTo = useCallback(
    (values: PresetFormValues) => {
      form.reset(values);
      setBaseline(values);
    },
    [form]
  );

  useEffect(() => {
    if (preset && destinations.data) {
      resetTo(presetToFormValues(preset, destinations.data) as PresetFormValues);
    }
  }, [preset, destinations.data, resetTo]);

  const values = form.watch();
  const dirty = baseline !== null && snapshot(values, preset) !== snapshot(baseline, preset);

  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  const leaveGuard = useUnsavedChangesGuard(dirty);

  const destinationId = values.destination_id;
  const selectedDestination = useMemo(
    () => (destinations.data ?? []).find((item) => item.id === destinationId),
    [destinations.data, destinationId]
  );
  const isNative = isNativeDestination(selectedDestination);

  useEffect(() => {
    if (isNative && form.getValues("mode") === "manifest_only") {
      form.setValue("mode", "deep", { shouldDirty: true });
    }
  }, [isNative, form]);

  const mode = values.mode;
  const runsOperations = modeOperations(modes.data, mode);
  const savedMode = baseline?.mode;
  const modeChanged = Boolean(savedMode && mode !== savedMode);
  const descriptionUnchanged = (values.description ?? "") === (baseline?.description ?? "");

  const save = useMutation({
    mutationFn: (body: PresetWrite) => (preset ? patchPreset(api, preset.id, body) : createPreset(api, body)),
    onSuccess: (saved) => {
      toast.success("Preset saved");
      if (destinations.data) resetTo(presetToFormValues(saved, destinations.data) as PresetFormValues);
      void queryClient.invalidateQueries({ queryKey: ["preset-simulation"] });
      onSaved();
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const [pendingSave, setPendingSave] = useState<PendingSave | null>(null);
  const [reviewing, setReviewing] = useState(false);

  const submit = async (formValues: PresetFormValues) => {
    const errors = validatePresetFormValues(formValues);
    if (errors) {
      for (const [key, message] of Object.entries(errors)) {
        form.setError(key as keyof PresetFormValues, { message });
      }
      return;
    }
    const operations = modeOperations(modes.data, formValues.mode);
    if (!operations) {
      toast.error("Can't save until this mode's operations have loaded.");
      return;
    }
    const body = formValuesToPresetWrite(formValues, preset, operations);
    const lifecycleChange = preset ? presetLifecycleWarning(preset, formValues, null) : null;
    const modeChange =
      preset && formValues.mode !== preset.mode
        ? { from: preset.mode, to: formValues.mode, diff: modeOperationDiff(modes.data, preset.mode, formValues.mode) }
        : null;
    if (!preset || (!lifecycleChange && !modeChange)) {
      save.mutate(body);
      return;
    }
    setReviewing(true);
    let simulation: PresetSimulationResponse | null = null;
    try {
      simulation = await queryClient.fetchQuery({
        queryKey: ["preset-simulation", preset.id, "review"],
        queryFn: () => simulatePreset(api, preset.id),
        staleTime: 30_000
      });
    } catch {
      simulation = null;
    } finally {
      setReviewing(false);
    }
    setPendingSave({
      body,
      lifecycle: lifecycleChange ? presetLifecycleWarning(preset, formValues, simulation?.matched_count ?? null) : null,
      modeChange,
      simulation
    });
  };

  const onSubmit = form.handleSubmit(submit);

  useEffect(() => {
    if (!dirty) return;
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void onSubmit();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dirty, onSubmit]);

  if (loading) return null;

  const disabled = role !== "admin";

  const modeOptions = (modes.data?.modes ?? []).map((info) => {
    const unsupported = isNative && info.mode === "manifest_only";
    return {
      value: info.mode,
      disabled: unsupported,
      label: (
        <span className="inline-flex items-center gap-2">
          {modeLabel(info.mode)}
          {info.recommended ? <Badge variant="secondary">Recommended</Badge> : null}
        </span>
      ),
      description: unsupported ? nativeUnsupportedOpTooltip : info.description
    };
  });

  return (
    <div className="flex w-full flex-col gap-4">
      <FormProvider {...form}>
        <form className="flex flex-col gap-4" onSubmit={onSubmit} aria-label="Preset configuration">
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
          <div className="space-y-1">
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
            {modeChanged && descriptionUnchanged && values.description ? (
              <p className="text-xs text-[var(--color-status-warning-fg)]" role="status">
                Mode changed from {modeLabel(savedMode ?? "")} to {modeLabel(mode)}. Update the description if it
                still describes the old mode.
              </p>
            ) : null}
          </div>
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

          <fieldset className="space-y-2" disabled={disabled}>
            <legend id="preset-mode-label" className="mb-1.5 text-sm font-medium text-foreground">
              Mode
            </legend>
            {modes.isError ? (
              <Alert
                variant="warning"
                title="Couldn't load the maintenance modes"
                description={(modes.error as Error)?.message ?? "Try reloading the page."}
              />
            ) : (
              <RadioGroup
                variant="card"
                name="mode"
                aria-labelledby="preset-mode-label"
                value={mode}
                disabled={disabled}
                onChange={(next) => form.setValue("mode", next as PresetFormValues["mode"], { shouldDirty: true })}
                options={modeOptions}
              />
            )}
            <div
              className="rounded-[var(--radius-default)] bg-[var(--color-surface-sunken)] px-3 py-2 text-sm"
              aria-live="polite"
              data-testid="mode-operations"
            >
              {runsOperations ? (
                <>
                  <span className="text-muted-foreground">{modeLabel(mode)} runs: </span>
                  {runsOperations.map((op, idx) => (
                    <span key={op} className="inline-flex items-center gap-1">
                      <code className="mono-id text-xs">{operationLabel(op)}</code>
                      {idx < runsOperations.length - 1 ? (
                        <span aria-hidden className="mx-0.5 text-muted-foreground">
                          →
                        </span>
                      ) : null}
                    </span>
                  ))}
                  <p className="mt-1 text-xs text-muted-foreground">
                    The planner still skips an operation a table does not need. Orphan cleanup and manifest rewrite
                    follow the thresholds below.
                  </p>
                </>
              ) : (
                <span className="text-muted-foreground">
                  {modes.isLoading ? "Loading what this mode runs…" : "This mode's operations are unavailable."}
                </span>
              )}
            </div>
          </fieldset>

          <DeepModeFields catalog={modes.data} operations={runsOperations} disabled={disabled} />

          <div className="flex flex-wrap gap-3">
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
                checked={values.enabled}
                onCheckedChange={(checked) => form.setValue("enabled", checked, { shouldDirty: true })}
              />
              <Label htmlFor="preset-enabled">Enabled</Label>
            </div>
            <div className="flex items-center gap-2 pt-6">
              <Switch
                id="preset-paused"
                disabled={disabled}
                checked={values.paused}
                onCheckedChange={(checked) => form.setValue("paused", checked, { shouldDirty: true })}
              />
              <Label htmlFor="preset-paused">Paused</Label>
            </div>
          </div>
          <p className="-mt-2 text-xs text-muted-foreground">
            Paused holds this preset&apos;s tables: they keep it and wait, nothing new runs, and its queued jobs are
            cancelled (admitted and running jobs finish). Turning Enabled off retires the preset, and its pinned
            tables fall through to rules or the default.
          </p>

          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Switch
                id="preset-default"
                disabled={disabled}
                checked={values.is_default}
                onCheckedChange={(checked) => form.setValue("is_default", checked, { shouldDirty: true })}
              />
              <Label htmlFor="preset-default">Default preset</Label>
            </div>
            <p className="text-xs text-muted-foreground">
              Runs for every table that has no pinned preset and matches no rule, including newly discovered tables.
              Needs a schedule. Only one preset can be the default.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="schedule_cron">Schedule cron</Label>
            <CronInput
              allowEmpty
              value={values.schedule_cron ?? ""}
              onChange={(cron) => form.setValue("schedule_cron", cron, { shouldDirty: true })}
            />
            {form.formState.errors.schedule_cron?.message ? (
              <p className="text-xs text-destructive" role="alert">
                {form.formState.errors.schedule_cron.message}
              </p>
            ) : null}
          </div>

          <ContinuousFields disabled={disabled} />

          <PresetConfigFields destinations={destinations.data ?? []} />

          {dirty && !disabled ? (
            <div
              role="region"
              aria-label="Unsaved changes"
              className="sticky bottom-0 z-20 -mx-4 mt-2 flex flex-wrap items-center gap-3 border-t border-border bg-background/95 px-4 py-3 shadow-[0_-4px_12px_rgba(0,0,0,0.06)] backdrop-blur"
            >
              <span className="inline-flex items-center gap-2 text-sm font-medium">
                <span aria-hidden className="size-2 rounded-full bg-[var(--color-status-warning)]" />
                Unsaved changes
              </span>
              <span className="hidden text-xs text-muted-foreground sm:inline">⌘S / Ctrl+S to save</span>
              <div className="ml-auto flex gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  disabled={save.isPending}
                  onClick={() => {
                    if (baseline) resetTo(baseline);
                  }}
                >
                  Discard
                </Button>
                <Button
                  type="submit"
                  variant="primary"
                  disabled={!modes.data}
                  loading={save.isPending || reviewing}
                >
                  Save preset
                </Button>
              </div>
            </div>
          ) : null}
        </form>
      </FormProvider>

      <AlertDialog open={pendingSave !== null} onOpenChange={(open) => (open ? null : setPendingSave(null))}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {pendingSave?.modeChange
                ? `Switch to ${modeLabel(pendingSave.modeChange.to)} mode?`
                : "Change who maintains these tables?"}
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-sm" data-testid="save-review">
                {pendingSave?.modeChange ? (
                  <ModeChangeSummary change={pendingSave.modeChange} simulation={pendingSave.simulation} />
                ) : null}
                {pendingSave?.lifecycle ? <p>{pendingSave.lifecycle}</p> : null}
              </div>
            </AlertDialogDescription>
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

      <DiscardChangesDialog
        open={leaveGuard.pendingHref !== null}
        onDiscard={leaveGuard.confirmLeave}
        onCancel={leaveGuard.cancelLeave}
      />
    </div>
  );
};

const ModeChangeSummary = ({
  change,
  simulation
}: {
  change: NonNullable<PendingSave["modeChange"]>;
  simulation: PresetSimulationResponse | null;
}) => {
  const backlog = formatOrphanBacklog(simulation?.orphan_backlog_bytes, simulation?.orphan_backlog_tables);
  const added = change.diff?.added ?? [];
  const removed = change.diff?.removed ?? [];
  return (
    <>
      <p>
        {modeLabel(change.from)} → {modeLabel(change.to)}.{" "}
        {simulation
          ? `The next scheduled run applies it to ${formatNumber(simulation.matched_count)} ${
              simulation.matched_count === 1 ? "table" : "tables"
            } this preset covers.`
          : "Couldn't count the tables this preset covers."}
      </p>
      {added.length > 0 ? (
        <p>
          <span className="font-medium text-foreground">Adds: </span>
          {added.map(operationLabel).join(", ")}
        </p>
      ) : null}
      {removed.length > 0 ? (
        <p>
          <span className="font-medium text-foreground">Stops: </span>
          {removed.map(operationLabel).join(", ")}
        </p>
      ) : null}
      {added.includes("remove_orphan_files") && backlog ? <p>{backlog}</p> : null}
    </>
  );
};
