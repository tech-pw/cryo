import { Plus } from "lucide-react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useFieldArray } from "react-hook-form";
import { toast } from "sonner";
import { testDestinationConfig } from "../../api/client";
import type { DestinationRead, DestinationWrite, EngineType, SourceRead } from "../../api/types";
import { QueryLogIngestionFields } from "../../components/QueryLogIngestionFields";
import { HintLabel } from "../../components/common";
import { Alert } from "../../components/ui/alert";
import { Autocomplete } from "../../components/ui/autocomplete";
import { Button } from "../../components/ui/button";
import { Collapsible } from "../../components/ui/collapsible";
import { FormField, FormProvider, useForm } from "../../components/ui/form";
import { FormErrorSummary } from "../../components/ui/form-error-summary";
import { Input } from "../../components/ui/input";
import { InputNumber } from "../../components/ui/input-number";
import { Label } from "../../components/ui/label";
import { MultiSelect, Select } from "../../components/ui/select";
import { Separator } from "../../components/ui/separator";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "../../components/ui/sheet";
import { Switch } from "../../components/ui/switch";
import { Textarea } from "../../components/ui/textarea";
import { useAuth } from "../../lib/auth";
import {
  engineVirtualFromDestination,
  engineVirtualOptions,
  parseEngineVirtual
} from "../../lib/engines";
import { getRuntimeFlags } from "../../lib/runtimeFlags";
import { sourceCatalogName, type CatalogBindingFormValue } from "../../lib/catalogBindings";
import { AWS_REGIONS } from "./constants";
import {
  buildDestinationConfig,
  buildQueryLogConfig,
  decomposeDestinationConfig,
  type DestinationFormValues,
  type TrinoCoordinatorFormValue
} from "./destinationConfig";

const buildDefaultValues = (value: DestinationRead | "new" | null): DestinationFormValues => {
  const isEditing = value && value !== "new";
  const initialEngine = isEditing ? value.engine : "trino";
  const initialEngineVirtual = isEditing
    ? engineVirtualFromDestination(value.engine, value.config)
    : "trino";
  const existingConfig = isEditing
    ? decomposeDestinationConfig(initialEngine, value.config as Record<string, unknown>)
    : { engine_virtual: "trino" as const, catalog_bindings: [] as CatalogBindingFormValue[] };
  return {
    name: isEditing ? value.name : "",
    engine: initialEngine,
    engine_virtual: initialEngineVirtual,
    secret_ref: isEditing ? value.secret_ref : null,
    enabled: isEditing ? value.enabled : true,
    offpeak_start: isEditing ? value.offpeak_start : 0,
    offpeak_end: isEditing ? value.offpeak_end : 0,
    coordinators: [{ host: "", port: 8080, http_scheme: "http" }],
    ...existingConfig
  } as DestinationFormValues;
};

export const DestinationDrawer = ({
  value,
  sources,
  saving,
  onClose,
  onSave
}: {
  value: DestinationRead | "new" | null;
  sources: SourceRead[];
  saving: boolean;
  onClose: () => void;
  onSave: (body: DestinationWrite) => void;
}) => {
  const { api, token } = useAuth();
  const runtime = useQuery({
    queryKey: ["runtime-flags", token],
    queryFn: () => getRuntimeFlags(api)
  });
  const engineOptions = engineVirtualOptions({ nativeEnabled: runtime.data?.native_engine_enabled });
  const isEditing = value && value !== "new";
  const initialEngine = isEditing ? value.engine : "trino";
  const [engine, setEngine] = useState<EngineType>(initialEngine as EngineType);
  const [testPassed, setTestPassed] = useState(false);

  const form = useForm<DestinationFormValues>({
    defaultValues: buildDefaultValues(value)
  });

  const { fields: coordinatorFields, append: appendCoordinator, remove: removeCoordinator } = useFieldArray({
    control: form.control,
    name: "coordinators"
  });

  useEffect(() => {
    if (!value) return;
    const defaults = buildDefaultValues(value);
    form.reset(defaults);
    setEngine((defaults.engine as EngineType) ?? "trino");
    setTestPassed(false);
  }, [value, form]);

  const testConnection = useMutation({
    mutationFn: () => {
      const current = form.getValues();
      return testDestinationConfig(api, {
        engine: current.engine,
        config: buildDestinationConfig(current),
        destination_id: isEditing ? value.id : undefined
      });
    },
    onSuccess: (data) => {
      setTestPassed(Boolean(data.ok));
      if (!data.ok) toast.error(data.message ?? "Test failed");
    },
    onError: () => {
      setTestPassed(false);
      toast.error("Spark submit params must be valid JSON before testing.");
    }
  });

  useEffect(() => {
    const subscription = form.watch((formValues, { name }) => {
      if (!name) return;
      if (name === "engine_virtual") {
        const parsed = parseEngineVirtual(formValues.engine_virtual ?? "trino");
        setEngine(parsed.engine);
        form.setValue("engine", parsed.engine);
        form.setValue("engine_impl", parsed.engine_impl);
      } else if (name === "engine") {
        setEngine((formValues.engine as EngineType) ?? "trino");
      }
      setTestPassed(false);
      testConnection.reset();
    });
    return () => subscription.unsubscribe();
  }, [form, testConnection]);

  const useBasicAuth = form.watch("use_basic_auth");
  const catalogBindings = form.watch("catalog_bindings") ?? [];
  const sourceById = new Map(sources.map((s) => [s.id, s]));
  const bindingSourceIds = catalogBindings.map((b) => b.source_id).filter(Boolean);

  const submit = (values: DestinationFormValues) => {
    if (!values.name.trim()) {
      form.setError("name", { message: "Name is required" });
      return;
    }
    if (values.engine === "trino") {
      const coordinators = values.coordinators ?? [];
      if (coordinators.some((c) => !c.host?.trim())) {
        form.setError("coordinators", { message: "Each coordinator needs a host" });
        return;
      }
    }
    if (values.engine === "spark_emr" && !values.application_id?.trim()) {
      form.setError("application_id", { message: "EMR application ID is required" });
      return;
    }
    if (values.engine === "spark_emr" && !values.execution_role_arn?.trim()) {
      form.setError("execution_role_arn", { message: "Execution role ARN is required" });
      return;
    }
    try {
      const config = buildDestinationConfig(values);
      const query_log_config = buildQueryLogConfig(values);
      const body: Parameters<typeof onSave>[0] = {
        name: values.name,
        engine: values.engine,
        config,
        enabled: values.enabled,
        offpeak_start: values.offpeak_start ?? null,
        offpeak_end: values.offpeak_end ?? null,
        query_log_config,
        query_log_retention_days: values.query_log_enabled ? values.query_log_retention_days ?? 30 : null
      };
      // The API never returns the stored secret_ref (DestinationRead omits it entirely), so this
      // field always starts blank on an edit. Only send it when the user actually typed something
      // -- otherwise the key is omitted from the request body, so patch_destination's
      // `exclude_unset` leaves the existing secret_ref untouched instead of overwriting it with null.
      if (values.secret_ref?.trim()) {
        body.secret_ref = values.secret_ref.trim();
      }
      onSave(body);
    } catch {
      toast.error("Spark submit params must be valid JSON.");
    }
  };

  return (
    <Sheet open={Boolean(value)} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="flex h-full w-full flex-col overflow-y-auto sm:max-w-[620px]">
        <SheetHeader>
          <SheetTitle>{isEditing ? "Edit engine" : "New engine"}</SheetTitle>
        </SheetHeader>
        <FormProvider {...form}>
          <form className="flex flex-col gap-4" onSubmit={form.handleSubmit(submit)}>
            <FormErrorSummary<DestinationFormValues> labels={{ name: "Name" }} />

            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Identity</p>
            <FormField name="name" label="Name">
              {(field) => (
                <Input
                  placeholder="trino-primary"
                  value={String(field.value ?? "")}
                  onChange={field.onChange}
                  onBlur={field.onBlur}
                  name={field.name}
                  ref={field.ref}
                />
              )}
            </FormField>

            <FormField name="engine_virtual" label="Engine">
              {(field) => (
                <Select
                  value={String(field.value ?? "")}
                  onChange={(next) => field.onChange(next ?? "")}
                  options={engineOptions}
                  onBlur={field.onBlur}
                  name={field.name}
                  ref={field.ref}
                />
              )}
            </FormField>

            <input type="hidden" {...form.register("engine")} />
            <input type="hidden" {...form.register("engine_impl")} />

            {engine === "trino" ? (
              <>
                <div className="space-y-2">
                  <Label>
                    <HintLabel
                      label="Coordinators"
                      tooltip="One or more Trino coordinator hosts. Multiple entries let Cryo spread load or fail over."
                    />
                  </Label>
                  <div className="space-y-2">
                    {coordinatorFields.map((field, index) => (
                      <div key={field.id} className="flex flex-wrap items-center gap-2">
                        <Input
                          placeholder="trino.internal"
                          className="w-60"
                          value={form.watch(`coordinators.${index}.host`) ?? ""}
                          onChange={(event) =>
                            form.setValue(`coordinators.${index}.host`, event.target.value)
                          }
                        />
                        <InputNumber
                          className="w-24"
                          placeholder="8080"
                          min={1}
                          max={65535}
                          value={form.watch(`coordinators.${index}.port`) ?? 8080}
                          onChange={(port) =>
                            form.setValue(`coordinators.${index}.port`, port ?? 8080)
                          }
                        />
                        <Select
                          className="w-24"
                          value={form.watch(`coordinators.${index}.http_scheme`) ?? "http"}
                          onChange={(scheme) =>
                            form.setValue(
                              `coordinators.${index}.http_scheme`,
                              (scheme as TrinoCoordinatorFormValue["http_scheme"]) ?? "http"
                            )
                          }
                          options={[
                            { label: "http", value: "http" },
                            { label: "https", value: "https" }
                          ]}
                        />
                        {coordinatorFields.length > 1 ? (
                          <Button type="button" size="sm" variant="danger" onClick={() => removeCoordinator(index)}>
                            Remove
                          </Button>
                        ) : null}
                      </div>
                    ))}
                    <Button
                      type="button"
                      size="sm"
                      variant="secondary"
                      onClick={() => appendCoordinator({ host: "", port: 8080, http_scheme: "http" })}
                    >
                      <Plus className="size-3.5" aria-hidden="true" />
                      Add coordinator
                    </Button>
                  </div>
                </div>

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                  <FormField name="user" label="User">
                    {(field) => (
                      <Input
                        placeholder="cryo"
                        value={String(field.value ?? "")}
                        onChange={field.onChange}
                        onBlur={field.onBlur}
                        name={field.name}
                        ref={field.ref}
                      />
                    )}
                  </FormField>
                  <FormField
                    name="catalog"
                    label={
                      <HintLabel
                        label="Session default catalog"
                        tooltip="Trino session catalog used for connection probes. Per-table SQL catalog comes from lakehouse bindings below."
                      />
                    }
                  >
                    {(field) => (
                      <Input
                        placeholder="iceberg"
                        value={String(field.value ?? "")}
                        onChange={field.onChange}
                        onBlur={field.onBlur}
                        name={field.name}
                        ref={field.ref}
                      />
                    )}
                  </FormField>
                  <FormField name="schema" label="Schema (optional)">
                    {(field) => (
                      <Input
                        value={String(field.value ?? "")}
                        onChange={field.onChange}
                        onBlur={field.onBlur}
                        name={field.name}
                        ref={field.ref}
                      />
                    )}
                  </FormField>
                </div>

                <div className="space-y-1.5">
                  <Label>
                    <HintLabel
                      label="Lakehouses on this Trino"
                      tooltip="Select Cryo lakehouses whose catalogs are registered on this Trino cluster. Only tables from these lakehouses can use Trino presets that target this engine."
                    />
                  </Label>
                  <MultiSelect
                    placeholder="Select lakehouses"
                    value={bindingSourceIds}
                    options={sources.map((s) => ({
                      label: `${s.name} (${sourceCatalogName(s)})`,
                      value: s.id
                    }))}
                    onChange={(selected) => {
                      const prev = new Map(catalogBindings.map((b) => [b.source_id, b]));
                      form.setValue(
                        "catalog_bindings",
                        selected.map((sourceId) => {
                          const existing = prev.get(sourceId);
                          return existing ?? { source_id: sourceId, trino_catalog: undefined };
                        })
                      );
                    }}
                  />
                </div>

                {catalogBindings.length > 0 ? (
                  <div className="space-y-3">
                    {catalogBindings.map((binding, index) => {
                      const source = binding.source_id ? sourceById.get(binding.source_id) : undefined;
                      const placeholder = source ? sourceCatalogName(source) : "trino catalog name";
                      return (
                        <FormField
                          key={binding.source_id ?? index}
                          name={`catalog_bindings.${index}.trino_catalog`}
                          label={
                            <HintLabel
                              label={`Trino catalog for ${source?.name ?? binding.source_id ?? "lakehouse"}`}
                              tooltip="Optional override when the Trino catalog name differs from the Cryo lakehouse catalog name. Leave blank to use the lakehouse catalog name."
                            />
                          }
                        >
                          {(field) => (
                            <Input
                              placeholder={placeholder}
                              value={String(field.value ?? "")}
                              onChange={field.onChange}
                              onBlur={field.onBlur}
                              name={field.name}
                              ref={field.ref}
                            />
                          )}
                        </FormField>
                      );
                    })}
                    {catalogBindings.map((binding, index) => (
                      <input
                        key={`sid-${binding.source_id}`}
                        type="hidden"
                        value={binding.source_id}
                        {...form.register(`catalog_bindings.${index}.source_id`)}
                      />
                    ))}
                  </div>
                ) : null}

                <div className="flex items-center gap-2">
                  <Switch
                    id="use_basic_auth"
                    checked={Boolean(useBasicAuth)}
                    onCheckedChange={(checked) => form.setValue("use_basic_auth", checked)}
                  />
                  <Label htmlFor="use_basic_auth">Use basic auth</Label>
                </div>

                {useBasicAuth ? (
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <FormField name="auth_username" label="Username">
                      {(field) => (
                        <Input
                          autoComplete="off"
                          value={String(field.value ?? "")}
                          onChange={field.onChange}
                          onBlur={field.onBlur}
                          name={field.name}
                          ref={field.ref}
                        />
                      )}
                    </FormField>
                    <FormField name="auth_password" label="Password">
                      {(field) => (
                        <Input
                          type="password"
                          autoComplete="off"
                          placeholder={form.watch("auth_has_password") ? "Leave blank to keep the saved password" : ""}
                          value={String(field.value ?? "")}
                          onChange={field.onChange}
                          onBlur={field.onBlur}
                          name={field.name}
                          ref={field.ref}
                        />
                      )}
                    </FormField>
                  </div>
                ) : null}
                {useBasicAuth && form.watch("auth_has_password") ? (
                  <p className="text-xs text-muted-foreground">
                    A password is already saved for this destination. Leave the field blank to keep it, or enter a
                    new value to replace it.
                  </p>
                ) : null}
              </>
            ) : engine === "spark_emr" ? (
              <>
                <FormField name="application_id" label="EMR Serverless application ID">
                  {(field) => (
                    <Input
                      placeholder="00fcxxxxxxxxxxxx"
                      value={String(field.value ?? "")}
                      onChange={field.onChange}
                      onBlur={field.onBlur}
                      name={field.name}
                      ref={field.ref}
                    />
                  )}
                </FormField>
                <FormField name="execution_role_arn" label="Execution role ARN">
                  {(field) => (
                    <Input
                      placeholder="arn:aws:iam::123456789012:role/cryo-emr-execution"
                      value={String(field.value ?? "")}
                      onChange={field.onChange}
                      onBlur={field.onBlur}
                      name={field.name}
                      ref={field.ref}
                    />
                  )}
                </FormField>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                  <FormField name="region" label="AWS region">
                    {(field) => (
                      <Autocomplete
                        value={String(field.value ?? "")}
                        onChange={field.onChange}
                        options={AWS_REGIONS.map((region) => ({ label: region.value, value: region.value }))}
                        placeholder="us-east-1"
                        filterOption={(input, option) =>
                          option.value.toLowerCase().includes(input.toLowerCase())
                        }
                      />
                    )}
                  </FormField>
                  <FormField name="s3_logs_bucket" label="S3 logs bucket (optional)">
                    {(field) => (
                      <Input
                        placeholder="my-emr-logs"
                        value={String(field.value ?? "")}
                        onChange={field.onChange}
                        onBlur={field.onBlur}
                        name={field.name}
                        ref={field.ref}
                      />
                    )}
                  </FormField>
                  <FormField name="s3_logs_prefix" label="S3 logs prefix">
                    {(field) => (
                      <Input
                        placeholder="cryo-spark-logs"
                        value={String(field.value ?? "")}
                        onChange={field.onChange}
                        onBlur={field.onBlur}
                        name={field.name}
                        ref={field.ref}
                      />
                    )}
                  </FormField>
                </div>
                <FormField name="entry_point" label="Spark entry point">
                  {(field) => (
                    <Input
                      placeholder="s3://cryo-artifacts/cryo_spark_runner.py"
                      value={String(field.value ?? "")}
                      onChange={field.onChange}
                      onBlur={field.onBlur}
                      name={field.name}
                      ref={field.ref}
                    />
                  )}
                </FormField>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <FormField
                    name="poll_interval_seconds"
                    label={
                      <HintLabel label="Poll interval (s)" tooltip="How often Cryo checks EMR for job status." />
                    }
                  >
                    {(field) => (
                      <InputNumber
                        min={1}
                        value={field.value as number | null | undefined}
                        onChange={field.onChange}
                        onBlur={field.onBlur}
                        name={field.name}
                        ref={field.ref}
                      />
                    )}
                  </FormField>
                  <FormField
                    name="timeout_seconds"
                    label={
                      <HintLabel label="Timeout (s)" tooltip="Cryo gives up waiting for the job after this long." />
                    }
                  >
                    {(field) => (
                      <InputNumber
                        min={1}
                        value={field.value as number | null | undefined}
                        onChange={field.onChange}
                        onBlur={field.onBlur}
                        name={field.name}
                        ref={field.ref}
                      />
                    )}
                  </FormField>
                </div>
                <Collapsible label="Advanced Spark submit parameters (optional)">
                  <p className="text-sm text-muted-foreground">
                    Extra <code>--conf</code> overrides passed to <code>spark-submit</code>, as JSON key/value pairs.
                  </p>
                  <FormField name="spark_submit_params_json" label="Spark submit params" className="mt-2">
                    {(field) => (
                      <Textarea
                        rows={4}
                        className="mono-input font-mono text-xs"
                        placeholder='{"--conf spark.executor.memory": "8g"}'
                        value={String(field.value ?? "")}
                        onChange={field.onChange}
                        onBlur={field.onBlur}
                        name={field.name}
                        ref={field.ref}
                      />
                    )}
                  </FormField>
                </Collapsible>
              </>
            ) : (
              <>
                <FormField
                  name="slot_cap"
                  label={
                    <HintLabel
                      label="Slot cap"
                      tooltip="Maximum concurrent native maintenance jobs the governor admits for this engine."
                    />
                  }
                >
                  {(field) => (
                    <InputNumber
                      min={1}
                      max={64}
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
                  name="operation_mode"
                  label={
                    <HintLabel
                      label="Operation mode"
                      tooltip="Passed to the cryo-engine subprocess execute payloads (default: execute)."
                    />
                  }
                >
                  {(field) => (
                    <Input
                      placeholder="execute"
                      value={String(field.value ?? "")}
                      onChange={field.onChange}
                      onBlur={field.onBlur}
                      name={field.name}
                      ref={field.ref}
                    />
                  )}
                </FormField>
                <FormField
                  name="timeout_seconds"
                  label={
                    <HintLabel
                      label="Timeout (s)"
                      tooltip="Cryo gives up waiting for the cryo-engine subprocess after this long."
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
                  name="binary_path"
                  label={
                    <HintLabel
                      label="Binary path (optional)"
                      tooltip="Path to cryo-engine-go or cryo-engine. Leave empty to use PATH or engine-go/bin/cryo-engine-go."
                    />
                  }
                >
                  {(field) => (
                    <Input
                      placeholder="/usr/local/bin/cryo-engine-go"
                      value={String(field.value ?? "")}
                      onChange={field.onChange}
                      onBlur={field.onBlur}
                      name={field.name}
                      ref={field.ref}
                    />
                  )}
                </FormField>
              </>
            )}

            <Button type="button" variant="secondary" loading={testConnection.isPending} onClick={() => testConnection.mutate()}>
              Test connection
            </Button>
            {testConnection.isPending ? (
              <p className="text-sm text-muted-foreground">
                This can take up to 2 minutes on slow or VPN-gated networks — sit back and relax.
              </p>
            ) : null}

            {testConnection.data ? (
              <Alert
                variant={testConnection.data.ok ? "success" : "error"}
                title={testConnection.data.ok ? "Connection succeeded" : "Connection failed"}
                description={
                  testConnection.data.ok
                    ? testConnection.data.message ??
                      `Capacity: ${testConnection.data.used ?? 0}/${testConnection.data.capacity ?? 0} in use`
                    : testConnection.data.message
                }
              />
            ) : null}

            <Separator />
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Scheduling</p>
            <div className="flex flex-wrap gap-3">
              <FormField
                name="offpeak_start"
                label={
                  <HintLabel
                    label="Off-peak start (hr)"
                    tooltip="IST hour when heavy (standard/deep) maintenance may start. Leave start and end both 0 (or equal) to allow maintenance at any time."
                  />
                }
              >
                {(field) => (
                  <InputNumber
                    min={0}
                    max={23}
                    value={field.value as number | null | undefined}
                    onChange={field.onChange}
                    onBlur={field.onBlur}
                    name={field.name}
                    ref={field.ref}
                  />
                )}
              </FormField>
              <FormField
                name="offpeak_end"
                label={
                  <HintLabel
                    label="Off-peak end (hr)"
                    tooltip="IST hour when the off-peak window ends (exclusive). Equal to start (including 0/0) disables the gate."
                  />
                }
              >
                {(field) => (
                  <InputNumber
                    min={0}
                    max={23}
                    value={field.value as number | null | undefined}
                    onChange={field.onChange}
                    onBlur={field.onBlur}
                    name={field.name}
                    ref={field.ref}
                  />
                )}
              </FormField>
            </div>

            <FormField name="secret_ref" label="Secret reference">
              {(field) => (
                <Input
                  placeholder="Encrypted secret reference, never raw credentials"
                  value={String(field.value ?? "")}
                  onChange={field.onChange}
                  onBlur={field.onBlur}
                  name={field.name}
                  ref={field.ref}
                />
              )}
            </FormField>
            {isEditing ? (
              <p className="text-xs text-muted-foreground -mt-2">
                The API never returns the saved reference, so this field starts blank. Leave it blank to keep the
                current reference unchanged, or enter a new value to replace it.
              </p>
            ) : null}

            <div className="flex items-center gap-2">
              <Switch
                id="enabled"
                checked={form.watch("enabled")}
                onCheckedChange={(checked) => form.setValue("enabled", checked)}
              />
              <Label htmlFor="enabled">Enabled</Label>
            </div>

            {engine === "trino" ? <QueryLogIngestionFields engine={engine} /> : null}

            {!testPassed ? (
              <Alert
                variant="warning"
                title="Test the connection before saving"
                description="Click &quot;Test connection&quot; above with a passing result to enable Save."
              />
            ) : null}

            <Separator />
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Review</p>
            <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-2">
              <div className="flex justify-between gap-2 sm:block">
                <dt className="text-muted-foreground">Name</dt>
                <dd className="font-medium">{form.watch("name") || "—"}</dd>
              </div>
              <div className="flex justify-between gap-2 sm:block">
                <dt className="text-muted-foreground">Engine</dt>
                <dd className="font-medium">{engine}</dd>
              </div>
              {engine === "trino" ? (
                <div className="flex justify-between gap-2 sm:block">
                  <dt className="text-muted-foreground">Coordinators</dt>
                  <dd className="font-medium">{coordinatorFields.length}</dd>
                </div>
              ) : null}
              {engine === "spark_emr" ? (
                <>
                  <div className="flex justify-between gap-2 sm:block">
                    <dt className="text-muted-foreground">EMR application ID</dt>
                    <dd className="font-medium">{form.watch("application_id") || "—"}</dd>
                  </div>
                  <div className="flex justify-between gap-2 sm:block">
                    <dt className="text-muted-foreground">AWS region</dt>
                    <dd className="font-medium">{form.watch("region") || "—"}</dd>
                  </div>
                </>
              ) : null}
              <div className="flex justify-between gap-2 sm:block">
                <dt className="text-muted-foreground">Off-peak window</dt>
                <dd className="font-medium">
                  {form.watch("offpeak_start")}:00–{form.watch("offpeak_end")}:00 IST
                </dd>
              </div>
              <div className="flex justify-between gap-2 sm:block">
                <dt className="text-muted-foreground">Enabled</dt>
                <dd className="font-medium">{form.watch("enabled") ? "Yes" : "No"}</dd>
              </div>
              <div className="flex justify-between gap-2 sm:block">
                <dt className="text-muted-foreground">Connection test</dt>
                <dd className="font-medium">{testPassed ? "Passed" : "Not yet passed"}</dd>
              </div>
            </dl>

            <Button type="submit" loading={saving} disabled={!testPassed}>
              Save
            </Button>
          </form>
        </FormProvider>
      </SheetContent>
    </Sheet>
  );
};
