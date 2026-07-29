import { Bot } from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  testAIProviderConfig,
  type AIProviderRead,
  type AIProviderType,
  type AIProviderWrite
} from "../../api/client";
import { HintLabel } from "../../components/common";
import { Alert } from "../../components/ui/alert";
import { Autocomplete } from "../../components/ui/autocomplete";
import { Button } from "../../components/ui/button";
import { FormField, FormProvider, useForm } from "../../components/ui/form";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Select } from "../../components/ui/select";
import { Separator } from "../../components/ui/separator";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "../../components/ui/sheet";
import { Switch } from "../../components/ui/switch";
import { useAuth } from "../../lib/auth";
import type { AIProviderFormValues } from "./aiProviderTypes";
import { DEFAULT_PROVIDER_TYPES } from "./aiProviderTypes";

export const AIProviderDrawer = ({
  value,
  providerTypes,
  saving,
  onClose,
  onSave
}: {
  value: AIProviderRead | "new" | null;
  providerTypes: AIProviderType[];
  saving: boolean;
  onClose: () => void;
  onSave: (body: AIProviderWrite & { id?: string }) => void;
}) => {
  const { api } = useAuth();
  const isEditing = value && value !== "new";
  const existingConfig = isEditing ? (value.config as Record<string, string>) : {};
  const effectiveProviderTypes = providerTypes.length > 0 ? providerTypes : DEFAULT_PROVIDER_TYPES;
  const [selectedType, setSelectedType] = useState<string>(isEditing ? value.provider_type : "anthropic");
  const [testPassed, setTestPassed] = useState(false);

  const form = useForm<AIProviderFormValues>({
    defaultValues: {
      name: "",
      provider_type: "anthropic",
      api_key: "",
      model: "claude-sonnet-4-20250514",
      enabled: true,
      is_default: false,
      config_api_base: "",
      config_api_version: "2024-03-01-preview"
    }
  });

  useEffect(() => {
    if (!value) return;
    if (value === "new") {
      form.reset({
        name: "",
        provider_type: "anthropic",
        api_key: "",
        model: "claude-sonnet-4-20250514",
        enabled: true,
        is_default: false,
        config_api_base: "",
        config_api_version: "2024-03-01-preview"
      });
      setSelectedType("anthropic");
    } else {
      form.reset({
        name: value.name,
        provider_type: value.provider_type,
        api_key: "",
        model: value.model,
        enabled: value.enabled,
        is_default: value.is_default,
        config_api_base: existingConfig.api_base ?? "",
        config_api_version: existingConfig.api_version ?? "2024-03-01-preview"
      });
      setSelectedType(value.provider_type);
    }
    setTestPassed(false);
  }, [value, form, existingConfig.api_base, existingConfig.api_version]);

  const testConnection = useMutation({
    mutationFn: () => {
      const current = form.getValues();
      const config: Record<string, string> = {};
      if (current.config_api_base) config.api_base = current.config_api_base;
      if (current.config_api_version) config.api_version = current.config_api_version;
      return testAIProviderConfig(api, {
        provider_type: current.provider_type,
        api_key: current.api_key || "",
        model: current.model,
        config,
        provider_id: isEditing ? value.id : undefined
      });
    },
    onSuccess: (data) => {
      setTestPassed(data.success);
      if (!data.success) toast.error(`Test failed: ${data.error}`);
      else toast.success(`AI responded: "${data.response}"`);
    },
    onError: (error) => {
      setTestPassed(false);
      toast.error((error as Error).message);
    }
  });

  useEffect(() => {
    const subscription = form.watch((_values, { name }) => {
      if (name) {
        if (name === "provider_type") {
          const providerType = form.getValues("provider_type");
          setSelectedType(providerType);
          const newTypeInfo = effectiveProviderTypes.find((t) => t.type === providerType);
          if (newTypeInfo) {
            form.setValue("model", newTypeInfo.default_model);
            newTypeInfo.config_fields?.forEach((field) => {
              if (field.default) {
                form.setValue(`config_${field.name}` as keyof AIProviderFormValues, field.default);
              }
            });
          }
        }
        setTestPassed(false);
        testConnection.reset();
      }
    });
    return () => subscription.unsubscribe();
  }, [form, testConnection, effectiveProviderTypes]);

  const currentTypeInfo = effectiveProviderTypes.find((t) => t.type === selectedType);
  const modelOptions = currentTypeInfo?.models.map((m) => ({ label: m, value: m })) ?? [];
  const configFields = currentTypeInfo?.config_fields ?? [];

  const submit = (values: AIProviderFormValues) => {
    if (!values.name.trim()) {
      form.setError("name", { message: "Name is required" });
      return;
    }
    if (!isEditing && !values.api_key) {
      form.setError("api_key", { message: "API key is required for new providers" });
      return;
    }
    if (!values.model.trim()) {
      form.setError("model", { message: "Model is required" });
      return;
    }
    for (const field of configFields) {
      if (field.required && !values[`config_${field.name}`]) {
        form.setError(`config_${field.name}` as keyof AIProviderFormValues, {
          message: `${field.label} is required`
        });
        return;
      }
    }

    const config: Record<string, string> = {};
    if (values.config_api_base) config.api_base = values.config_api_base;
    if (values.config_api_version) config.api_version = values.config_api_version;

    const body: AIProviderWrite & { id?: string } = {
      name: values.name,
      provider_type: values.provider_type,
      api_key: values.api_key || undefined,
      model: values.model,
      config: Object.keys(config).length > 0 ? config : undefined,
      enabled: values.enabled,
      is_default: values.is_default
    };
    if (isEditing) {
      body.id = value.id;
    }
    onSave(body);
  };

  return (
    <Sheet open={Boolean(value)} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="flex h-full w-full flex-col overflow-y-auto sm:max-w-[520px]">
        <SheetHeader>
          <SheetTitle>{isEditing ? "Edit AI provider" : "New AI provider"}</SheetTitle>
        </SheetHeader>
        <FormProvider {...form}>
          <form className="flex flex-col gap-4" onSubmit={form.handleSubmit(submit)}>
            <FormField name="name" label="Name">
              {(field) => (
                <Input
                  placeholder="anthropic-production"
                  value={String(field.value ?? "")}
                  onChange={field.onChange}
                  onBlur={field.onBlur}
                  name={field.name}
                  ref={field.ref}
                />
              )}
            </FormField>

            <FormField name="provider_type" label="Provider">
              {(field) => (
                <Select
                  value={String(field.value ?? "")}
                  onChange={(next) => field.onChange(next ?? "")}
                  options={effectiveProviderTypes.map((t) => ({
                    label: t.name,
                    value: t.type
                  }))}
                  onBlur={field.onBlur}
                  name={field.name}
                  ref={field.ref}
                />
              )}
            </FormField>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Bot className="size-3.5" aria-hidden="true" />
              <span>{currentTypeInfo?.name ?? selectedType}</span>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="api_key">
                <HintLabel
                  label="API Key"
                  tooltip={
                    isEditing && value.has_api_key
                      ? "Leave blank to keep the existing key. Enter a new value to replace it."
                      : "Your API key from the provider's dashboard. Stored encrypted."
                  }
                />
              </Label>
              <Input
                id="api_key"
                type="password"
                  placeholder={isEditing && value.has_api_key ? "••••••••••••••••" : "sk-..."}
                autoComplete="off"
                value={form.watch("api_key")}
                onChange={(event) => form.setValue("api_key", event.target.value)}
              />
              {form.formState.errors.api_key?.message ? (
                <p className="text-xs text-destructive" role="alert">{form.formState.errors.api_key.message}</p>
              ) : null}
            </div>

            {configFields.map((field, index) => (
              <FormField
                key={field.name || `config-field-${index}`}
                name={`config_${field.name}` as keyof AIProviderFormValues}
                label={field.label}
              >
                {(formField) => (
                  <Input
                    placeholder={field.placeholder ?? ""}
                    value={String(formField.value ?? "")}
                    onChange={formField.onChange}
                    onBlur={formField.onBlur}
                    name={formField.name}
                    ref={formField.ref}
                  />
                )}
              </FormField>
            ))}

            <FormField
              name="model"
              label={selectedType === "azure_openai" ? "Deployment Name" : "Model"}
            >
              {(field) => (
                <Autocomplete
                  value={String(field.value ?? "")}
                  onChange={field.onChange}
                  options={modelOptions}
                  placeholder={selectedType === "azure_openai" ? "e.g. gpt-4o or my-custom-deployment" : "e.g. gpt-4o"}
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

            <Separator />

            <div className="flex items-center gap-2">
              <Switch
                id="enabled"
                checked={form.watch("enabled")}
                onCheckedChange={(checked) => form.setValue("enabled", checked)}
              />
              <Label htmlFor="enabled">Enabled</Label>
            </div>

            <div className="flex items-center gap-2">
              <Switch
                id="is_default"
                checked={form.watch("is_default")}
                onCheckedChange={(checked) => form.setValue("is_default", checked)}
              />
              <Label htmlFor="is_default">
                <HintLabel
                  label="Set as default"
                  tooltip="The default provider is used for AI chat and copilot features. Only one provider can be default."
                />
              </Label>
            </div>

            <Alert
              variant="info"
              title="API keys are stored encrypted"
              description="Keys are encrypted at rest and never logged. They're only used server-side to call the AI provider's API."
            />

            {!testPassed ? (
              <Alert
                variant="warning"
                title="Test the connection before saving"
                description="Click &quot;Test connection&quot; above with a passing result to enable Save."
              />
            ) : null}

            <Button type="submit" loading={saving} disabled={!testPassed}>
              Save
            </Button>
          </form>
        </FormProvider>
      </SheetContent>
    </Sheet>
  );
};
