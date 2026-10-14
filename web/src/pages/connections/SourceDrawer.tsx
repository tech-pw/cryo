import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { getSourceNamespaces, testSource } from "../../api/client";
import type { SourceRead, SourceWrite } from "../../api/types";
import { AwsIntegrationPanel } from "../../components/AwsIntegrationPanel";
import { S3LogConfigPanel } from "../../components/S3LogConfigPanel";
import { HintLabel } from "../../components/common";
import { Alert } from "../../components/ui/alert";
import { Autocomplete } from "../../components/ui/autocomplete";
import { Button } from "../../components/ui/button";
import { Collapsible } from "../../components/ui/collapsible";
import { FormErrorSummary } from "../../components/ui/form-error-summary";
import { FormField, FormProvider, useForm } from "../../components/ui/form";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { MultiSelect, Select } from "../../components/ui/select";
import { Separator } from "../../components/ui/separator";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "../../components/ui/sheet";
import { Switch } from "../../components/ui/switch";
import { Textarea } from "../../components/ui/textarea";
import { useAuth } from "../../lib/auth";
import { AWS_REGIONS, CATALOG_TYPES, CATALOG_URI_HINT, S3_AUTH_METHODS, type SourceFormValues } from "./constants";
import { buildSourceConfig, decomposeSourceConfig } from "./sourceConfig";
import { SourceScansPanel } from "./SourceScansPanel";
import { SourceDrawerSteps, type SourceDrawerStep } from "./SourceDrawerSteps";
import { CopyStorageConfigPicker } from "./CopyStorageConfigPicker";
import { SourceTestResultPanel } from "./SourceTestResultPanel";

const buildDefaultValues = (
  value: SourceRead | "new" | null
): SourceFormValues => {
  const isEditing = value && value !== "new";
  const existingConfig = isEditing ? decomposeSourceConfig(value.config as Record<string, unknown>) : {};
  return {
    name: isEditing ? value.name : "",
    type: isEditing ? value.type : "rest",
    secret_ref: isEditing ? value.secret_ref : null,
    enabled: isEditing ? value.enabled : true,
    maintenance_schema_names: isEditing ? value.maintenance_schema_names ?? [] : [],
    s3_auth_method: "default",
    ...existingConfig
  } as SourceFormValues;
};

export const SourceDrawer = ({
  value,
  otherSources,
  saving,
  onClose,
  onSave
}: {
  value: SourceRead | "new" | null;
  otherSources: SourceRead[];
  saving: boolean;
  onClose: () => void;
  onSave: (body: SourceWrite) => void;
}) => {
  const { api } = useAuth();
  const isEditing = value && value !== "new";
  const editingSource = isEditing ? value : null;
  const existingConfig = editingSource ? decomposeSourceConfig(editingSource.config as Record<string, unknown>) : {};
  const [catalogType, setCatalogType] = useState<string>(editingSource ? editingSource.type : "rest");
  const [s3AuthMethod, setS3AuthMethod] = useState<"default" | "access_key" | "assume_role">(
    (existingConfig as { s3_auth_method?: "default" | "access_key" | "assume_role" }).s3_auth_method ?? "default"
  );
  const [testPassed, setTestPassed] = useState(false);
  const [schemaOptions, setSchemaOptions] = useState<string[]>(
    editingSource ? editingSource.maintenance_schema_names ?? [] : []
  );

  // Stepping only applies to a fresh "new" source — an already-working connection has
  // nothing to progress through, so editing shows every section at once as before.
  const [step, setStep] = useState<SourceDrawerStep>("connect");
  const [furthestStep, setFurthestStep] = useState<SourceDrawerStep>("connect");
  const isStepped = !isEditing;

  const form = useForm<SourceFormValues>({
    defaultValues: buildDefaultValues(value)
  });

  useEffect(() => {
    if (!value) return;
    const defaults = buildDefaultValues(value);
    form.reset(defaults);
    setCatalogType(defaults.type);
    setS3AuthMethod(defaults.s3_auth_method ?? "default");
    setSchemaOptions(value !== "new" ? value.maintenance_schema_names ?? [] : []);
    setTestPassed(false);
    setStep("connect");
    setFurthestStep("connect");
  }, [value, form]);

  const savedNamespaces = useQuery({
    queryKey: ["source-namespaces", editingSource?.id ?? null],
    queryFn: () => getSourceNamespaces(api, editingSource!.id),
    enabled: Boolean(editingSource)
  });

  useEffect(() => {
    if (savedNamespaces.data?.ok) {
      setSchemaOptions(savedNamespaces.data.namespaces);
    }
  }, [savedNamespaces.data]);

  const testConnection = useMutation({
    mutationFn: () => {
      const current = form.getValues();
      return testSource(api, {
        type: current.type,
        config: buildSourceConfig(current),
        source_id: isEditing ? value.id : undefined
      });
    },
    onSuccess: (data) => {
      setTestPassed(Boolean(data.ok));
      if (data.ok) {
        setSchemaOptions(data.namespaces);
        setFurthestStep((prev) => (prev === "connect" ? "storage" : prev));
      }
    },
    onError: (error) => {
      setTestPassed(false);
      toast.error(
        error instanceof SyntaxError
          ? "Advanced properties must be valid JSON before testing."
          : error instanceof Error
            ? error.message
            : "Test failed"
      );
    }
  });

  useEffect(() => {
    const subscription = form.watch((formValues, { name }) => {
      if (!name) return;
      if (name === "type") setCatalogType(formValues.type ?? "rest");
      if (name === "s3_auth_method") {
        setS3AuthMethod(formValues.s3_auth_method ?? "default");
      }
      if (name === "maintenance_schema_names") return;
      setTestPassed(false);
      testConnection.reset();
    });
    return () => subscription.unsubscribe();
  }, [form, testConnection]);

  const maintenanceSchemaNames = form.watch("maintenance_schema_names") ?? [];
  const schemaSelectOptions = Array.from(
    new Set([
      ...schemaOptions,
      ...(isEditing && editingSource ? editingSource.maintenance_schema_names ?? [] : []),
      ...maintenanceSchemaNames
    ])
  );
  const showSchemaFilter =
    Boolean(testConnection.data?.ok) ||
    (Boolean(isEditing) && (savedNamespaces.data?.ok || schemaSelectOptions.length > 0));

  const submit = (values: SourceFormValues) => {
    if (!values.name.trim()) {
      form.setError("name", { message: "Name is required" });
      return;
    }
    if (values.type !== "glue" && !values.uri?.trim()) {
      form.setError("uri", { message: "Required for this catalog type" });
      return;
    }
    if (values.s3_auth_method === "assume_role" && !values.s3_role_arn?.trim()) {
      form.setError("s3_role_arn", { message: "Role ARN is required for assume role auth" });
      return;
    }
    try {
      const body: Parameters<typeof onSave>[0] = {
        name: values.name,
        type: values.type,
        config: buildSourceConfig(values),
        enabled: values.enabled,
        maintenance_schema_names: values.maintenance_schema_names ?? []
      };
      // The API never returns the stored secret_ref (SourceRead omits it entirely), so this field
      // always starts blank on an edit. Only send it when the user actually typed something --
      // otherwise the key is omitted from the request body, so patch_source's `exclude_unset`
      // leaves the existing secret_ref untouched instead of overwriting it with null.
      if (values.secret_ref?.trim()) {
        body.secret_ref = values.secret_ref.trim();
      }
      onSave(body);
    } catch {
      toast.error("Advanced properties must be valid JSON.");
    }
  };

  return (
    <Sheet open={Boolean(value)} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="flex h-full w-full flex-col overflow-y-auto sm:max-w-[620px]">
        <SheetHeader>
          <SheetTitle>{isEditing ? "Edit lakehouse" : "New lakehouse"}</SheetTitle>
          {isStepped ? (
            <SourceDrawerSteps current={step} furthestReached={furthestStep} onJump={setStep} />
          ) : null}
        </SheetHeader>
        <FormProvider {...form}>
          <form className="flex flex-col gap-4" onSubmit={form.handleSubmit(submit)}>
            <FormErrorSummary<SourceFormValues>
              labels={{
                name: "Name",
                uri: "Catalog URI",
                s3_role_arn: "Role ARN"
              }}
            />

            {!isStepped || step === "connect" ? (
              <>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Identity</p>
            <FormField name="name" label="Name">
              {(field) => (
                <Input
                  placeholder="production-lakehouse"
                  value={String(field.value ?? "")}
                  onChange={field.onChange}
                  onBlur={field.onBlur}
                  name={field.name}
                  ref={field.ref}
                />
              )}
            </FormField>

            <FormField name="type" label="Catalog type">
              {(field) => (
                <Select
                  value={String(field.value ?? "")}
                  onChange={(next) => field.onChange(next ?? "")}
                  options={CATALOG_TYPES}
                  onBlur={field.onBlur}
                  name={field.name}
                  ref={field.ref}
                />
              )}
            </FormField>

            {catalogType !== "glue" ? (
              <FormField name="uri" label="Catalog URI">
                {(field) => (
                  <Input
                    placeholder={CATALOG_URI_HINT[catalogType] ?? "http://localhost:8181"}
                    value={String(field.value ?? "")}
                    onChange={field.onChange}
                    onBlur={field.onBlur}
                    name={field.name}
                    ref={field.ref}
                  />
                )}
              </FormField>
            ) : null}

            <FormField name="warehouse" label="Warehouse">
              {(field) => (
                <Input
                  placeholder={catalogType === "glue" ? "s3://my-bucket/warehouse" : "warehouse"}
                  value={String(field.value ?? "")}
                  onChange={field.onChange}
                  onBlur={field.onBlur}
                  name={field.name}
                  ref={field.ref}
                />
              )}
            </FormField>

            <FormField
              name="catalog_name"
              label={
                <HintLabel
                  label="Catalog name"
                  tooltip="Overrides the name PyIceberg registers this catalog under. Leave blank to use the lakehouse name above."
                />
              }
            >
              {(field) => (
                <Input
                  placeholder="(defaults to the name above)"
                  value={String(field.value ?? "")}
                  onChange={field.onChange}
                  onBlur={field.onBlur}
                  name={field.name}
                  ref={field.ref}
                />
              )}
            </FormField>

            <Button type="button" variant="secondary" loading={testConnection.isPending} onClick={() => testConnection.mutate()}>
              Test connection
            </Button>
            {testConnection.isPending ? (
              <p className="text-sm text-muted-foreground">
                This can take up to 2 minutes on slow or VPN-gated networks — sit back and relax.
              </p>
            ) : null}
            {testConnection.data ? <SourceTestResultPanel result={testConnection.data} /> : null}

            {isStepped && testPassed ? (
              <Button
                type="button"
                className="self-end"
                onClick={() => {
                  setFurthestStep((prev) => (prev === "connect" ? "storage" : prev));
                  setStep("storage");
                }}
              >
                Continue to storage
                <ArrowRight />
              </Button>
            ) : null}
              </>
            ) : null}

            {isEditing ? (
              <>
                <Separator />
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Scans</p>
                <SourceScansPanel sourceId={value.id} />
              </>
            ) : null}

            {!isStepped || step === "storage" ? (
              <>
            <Separator />
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Storage connection</p>
            <p className="text-sm text-muted-foreground">
              Configure how Cryo authenticates to S3 storage. For production, use IAM roles.
              Access keys are only recommended for local/dev environments (e.g. MinIO).
            </p>

            {!isEditing ? (
              <CopyStorageConfigPicker
                otherSources={otherSources}
                onApply={(config) => {
                  form.setValue("s3_auth_method", config.s3_auth_method);
                  form.setValue("s3_region", config.s3_region ?? "");
                  form.setValue("s3_endpoint", config.s3_endpoint ?? "");
                  form.setValue("s3_role_arn", config.s3_role_arn ?? "");
                  form.setValue("s3_external_id", config.s3_external_id ?? "");
                  form.setValue("s3_role_session_name", config.s3_role_session_name ?? "");
                  form.setValue("s3_path_style_access", config.s3_path_style_access ?? false);
                  setS3AuthMethod(config.s3_auth_method);
                  toast.success("Storage auth copied — re-enter the secret key below if this source used one.");
                }}
              />
            ) : null}

            <FormField
              name="s3_auth_method"
              label={
                <HintLabel
                  label="Authentication method"
                  tooltip="How to authenticate to S3. IAM roles are recommended for production."
                />
              }
            >
              {(field) => (
                <Select
                  value={String(field.value ?? "default")}
                  onChange={(next) => field.onChange(next ?? "default")}
                  options={S3_AUTH_METHODS}
                  onBlur={field.onBlur}
                  name={field.name}
                  ref={field.ref}
                />
              )}
            </FormField>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <FormField name="s3_region" label="AWS Region">
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
              <FormField name="s3_endpoint" label="S3 endpoint (optional)">
                {(field) => (
                  <Input
                    placeholder="http://localhost:9000"
                    value={String(field.value ?? "")}
                    onChange={field.onChange}
                    onBlur={field.onBlur}
                    name={field.name}
                    ref={field.ref}
                  />
                )}
              </FormField>
            </div>

            {s3AuthMethod === "default" ? (
              <Alert
                variant="info"
                title="Using default AWS credentials"
                description="Cryo will use the AWS SDK default credential chain: environment variables, IAM role (IRSA/instance profile), or ~/.aws/credentials."
              />
            ) : null}

            {s3AuthMethod === "access_key" ? (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <FormField name="s3_access_key_id" label="Access key ID">
                  {(field) => (
                    <Input
                      autoComplete="off"
                      placeholder="AKIA..."
                      value={String(field.value ?? "")}
                      onChange={field.onChange}
                      onBlur={field.onBlur}
                      name={field.name}
                      ref={field.ref}
                    />
                  )}
                </FormField>
                <FormField name="s3_secret_access_key" label="Secret access key">
                  {(field) => (
                    <Input
                      type="password"
                      autoComplete="off"
                      placeholder={form.watch("s3_has_secret_access_key") ? "Leave blank to keep the saved key" : ""}
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

            {s3AuthMethod === "access_key" && form.watch("s3_has_secret_access_key") ? (
              <p className="text-xs text-muted-foreground -mt-1">
                A secret access key is already saved for this source. Leave the field blank to keep it, or enter a
                new value to replace it.
              </p>
            ) : null}

            {s3AuthMethod === "assume_role" ? (
              <>
                <FormField
                  name="s3_role_arn"
                  label={
                    <HintLabel
                      label="Role ARN"
                      tooltip="The ARN of the IAM role to assume for cross-account access."
                    />
                  }
                >
                  {(field) => (
                    <Input
                      placeholder="arn:aws:iam::123456789012:role/MyCrossAccountRole"
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
                    name="s3_external_id"
                    label={
                      <HintLabel
                        label="External ID (optional)"
                        tooltip="Stored with the lakehouse. Current catalog clients do not send this when assuming the role."
                      />
                    }
                  >
                    {(field) => (
                      <Input
                        placeholder="my-external-id"
                        value={String(field.value ?? "")}
                        onChange={field.onChange}
                        onBlur={field.onBlur}
                        name={field.name}
                        ref={field.ref}
                      />
                    )}
                  </FormField>
                  <FormField
                    name="s3_role_session_name"
                    label={
                      <HintLabel
                        label="Session name (optional)"
                        tooltip="Stored as the role session name. Blank saves nothing; nothing fills in cryo-session."
                      />
                    }
                  >
                    {(field) => (
                      <Input
                        placeholder="cryo-session"
                        value={String(field.value ?? "")}
                        onChange={field.onChange}
                        onBlur={field.onBlur}
                        name={field.name}
                        ref={field.ref}
                      />
                    )}
                  </FormField>
                </div>
              </>
            ) : null}

            <div className="flex items-center gap-2">
              <Switch
                id="s3_path_style_access"
                checked={Boolean(form.watch("s3_path_style_access"))}
                onCheckedChange={(checked) => form.setValue("s3_path_style_access", checked)}
              />
              <Label htmlFor="s3_path_style_access">Path-style access</Label>
            </div>

            <Collapsible label="Advanced properties (optional)">
              <p className="text-sm text-muted-foreground">
                Any other PyIceberg catalog property (OAuth credentials, custom headers, SSL settings, etc.) — merged
                in alongside the fields above.
              </p>
              <FormField name="advanced_json" label="Advanced JSON" className="mt-2">
                {(field) => (
                  <Textarea
                    rows={5}
                    className="mono-input font-mono text-xs"
                    placeholder='{"credential": "client-id:client-secret"}'
                    value={String(field.value ?? "")}
                    onChange={field.onChange}
                    onBlur={field.onBlur}
                    name={field.name}
                    ref={field.ref}
                  />
                )}
              </FormField>
            </Collapsible>

            {isEditing ? (
              <div className="space-y-3">
                <Collapsible label="AWS S3 cost metrics (free tier)">
                  <AwsIntegrationPanel sourceId={value.id} />
                </Collapsible>
                <Collapsible label="S3 request-cost tracking">
                  <S3LogConfigPanel sourceId={value.id} />
                </Collapsible>
              </div>
            ) : null}

            {isStepped ? (
              <div className="flex items-center justify-between">
                <Button type="button" variant="ghost" onClick={() => setStep("connect")}>
                  <ArrowLeft />
                  Back
                </Button>
                <Button
                  type="button"
                  onClick={() => {
                    setFurthestStep((prev) => (prev === "storage" ? "review" : prev));
                    setStep("review");
                  }}
                >
                  Continue to review
                  <ArrowRight />
                </Button>
              </div>
            ) : null}
              </>
            ) : null}

            {!isStepped || step === "review" ? (
              <>
            {showSchemaFilter ? (
              <>
                <Separator />
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Tuning</p>
              </>
            ) : null}
            {showSchemaFilter ? (
              <FormField
                name="maintenance_schema_names"
                label={
                  <HintLabel
                    label="Schemas for maintenance"
                    tooltip="Only tables in the selected schemas are scanned and eligible for maintenance. Leave empty to include every schema discovered in this catalog."
                  />
                }
              >
                {(field) => (
                  <MultiSelect
                    value={(field.value as string[] | undefined) ?? []}
                    onChange={field.onChange}
                    placeholder="All schemas (no filter)"
                    options={schemaSelectOptions.map((schema) => ({
                      value: schema,
                      label: schema || "(unnamed)"
                    }))}
                    onBlur={field.onBlur}
                    name={field.name}
                    ref={field.ref}
                  />
                )}
              </FormField>
            ) : null}

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

            <Separator />
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Review</p>
            <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-2">
              <div className="flex justify-between gap-2 sm:block">
                <dt className="text-muted-foreground">Name</dt>
                <dd className="font-medium">{form.watch("name") || "—"}</dd>
              </div>
              <div className="flex justify-between gap-2 sm:block">
                <dt className="text-muted-foreground">Catalog type</dt>
                <dd className="font-medium">{CATALOG_TYPES.find((t) => t.value === catalogType)?.label ?? catalogType}</dd>
              </div>
              <div className="flex justify-between gap-2 sm:block">
                <dt className="text-muted-foreground">S3 authentication</dt>
                <dd className="font-medium">
                  {S3_AUTH_METHODS.find((m) => m.value === form.watch("s3_auth_method"))?.label ?? "Default"}
                </dd>
              </div>
              <div className="flex justify-between gap-2 sm:block">
                <dt className="text-muted-foreground">Schemas for maintenance</dt>
                <dd className="font-medium">
                  {(form.watch("maintenance_schema_names") ?? []).length > 0
                    ? `${(form.watch("maintenance_schema_names") ?? []).length} selected`
                    : "All schemas"}
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

            {!testPassed ? (
              <Alert
                variant="warning"
                title="Test the connection before saving"
                description="Click &quot;Test connection&quot; above with a passing result to enable Save."
              />
            ) : null}

            <div className={isStepped ? "flex items-center justify-between" : undefined}>
              {isStepped ? (
                <Button type="button" variant="ghost" onClick={() => setStep("storage")}>
                  <ArrowLeft />
                  Back
                </Button>
              ) : null}
              <Button type="submit" loading={saving} disabled={!testPassed}>
                Save
              </Button>
            </div>
              </>
            ) : null}
          </form>
        </FormProvider>
      </SheetContent>
    </Sheet>
  );
};
