import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { testSlackConfigValues, type SlackConfigRead, type SlackConfigWrite } from "../../api/client";
import { CronInput } from "../../components/CronInput";
import { HintLabel } from "../../components/common";
import { Alert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { FormField, FormProvider, useForm } from "../../components/ui/form";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Separator } from "../../components/ui/separator";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "../../components/ui/sheet";
import { Switch } from "../../components/ui/switch";
import { useAuth } from "../../lib/auth";
import { validateCronExpression } from "../../lib/cron";
import type { SlackFormValues } from "./slackTypes";

export const SlackDrawer = ({
  value,
  saving,
  onClose,
  onSave
}: {
  value: SlackConfigRead | "new" | null;
  saving: boolean;
  onClose: () => void;
  onSave: (body: SlackConfigWrite & { id?: string }) => void;
}) => {
  const { api } = useAuth();
  const editingConfig = value !== "new" && value !== null ? value : null;
  const isEditing = editingConfig !== null;
  const [testPassed, setTestPassed] = useState(false);

  const form = useForm<SlackFormValues>({
    defaultValues: {
      name: "",
      webhook_url: "",
      channel: "",
      daily_report_enabled: true,
      daily_report_cron: "0 9 * * *",
      daily_report_use_ai: true,
      enabled: true,
      is_default: false
    }
  });

  useEffect(() => {
    if (!value) return;
    if (value === "new") {
      form.reset({
        name: "",
        webhook_url: "",
        channel: "",
        daily_report_enabled: true,
        daily_report_cron: "0 9 * * *",
        daily_report_use_ai: true,
        enabled: true,
        is_default: false
      });
    } else {
      form.reset({
        name: value.name,
        webhook_url: "",
        channel: value.channel ?? "",
        daily_report_enabled: value.daily_report_enabled,
        daily_report_cron: value.daily_report_cron,
        daily_report_use_ai: value.daily_report_use_ai,
        enabled: value.enabled,
        is_default: value.is_default
      });
    }
    setTestPassed(false);
  }, [value, form]);

  const testConnection = useMutation({
    mutationFn: () => {
      const current = form.getValues();
      return testSlackConfigValues(api, {
        webhook_url: current.webhook_url || "",
        channel: current.channel.trim() ? current.channel.trim() : null,
        config_id: isEditing ? editingConfig.id : undefined
      });
    },
    onSuccess: () => {
      setTestPassed(true);
      const channel = form.getValues("channel").trim();
      toast.success(
        channel
          ? "Test message sent to Slack!"
          : "Test message sent to Slack. No channel override was sent."
      );
    },
    onError: (error) => {
      setTestPassed(false);
      toast.error((error as Error).message);
    }
  });

  useEffect(() => {
    const subscription = form.watch((_values, { name }) => {
      if (name === "webhook_url" || name === "channel") {
        const clearing =
          isEditing &&
          !form.getValues("webhook_url").trim() &&
          !form.getValues("channel").trim();
        if (name === "channel" && clearing) return;
        setTestPassed(false);
        testConnection.reset();
      }
    });
    return () => subscription.unsubscribe();
  }, [form, isEditing, testConnection]);

  const submit = (values: SlackFormValues) => {
    if (!values.name.trim()) {
      form.setError("name", { message: "Name is required" });
      return;
    }
    if (!isEditing && !values.webhook_url) {
      form.setError("webhook_url", { message: "Webhook URL is required for new connections" });
      return;
    }
    const cronError = validateCronExpression(values.daily_report_cron);
    if (cronError) {
      form.setError("daily_report_cron", { message: cronError });
      return;
    }

    const body: SlackConfigWrite & { id?: string } = {
      name: values.name,
      webhook_url: values.webhook_url || undefined,
      channel: values.channel.trim() ? values.channel.trim() : null,
      daily_report_enabled: values.daily_report_enabled,
      daily_report_cron: values.daily_report_cron,
      daily_report_use_ai: values.daily_report_use_ai,
      enabled: values.enabled,
      is_default: values.is_default
    };
    if (isEditing && editingConfig) {
      body.id = editingConfig.id;
    }
    onSave(body);
  };

  return (
    <Sheet open={Boolean(value)} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="flex h-full w-full flex-col overflow-y-auto sm:max-w-[520px]">
        <SheetHeader>
          <SheetTitle>{isEditing ? "Edit Slack connection" : "New Slack connection"}</SheetTitle>
        </SheetHeader>
        <FormProvider {...form}>
          <form className="flex flex-col gap-4" onSubmit={form.handleSubmit(submit)}>
            <FormField name="name" label="Name">
              {(field) => (
                <Input
                  placeholder="daily-reports"
                  value={String(field.value ?? "")}
                  onChange={field.onChange}
                  onBlur={field.onBlur}
                  name={field.name}
                  ref={field.ref}
                />
              )}
            </FormField>

            <div className="space-y-1.5">
              <Label htmlFor="webhook_url">
                <HintLabel
                  label="Webhook URL"
                  tooltip={
                    isEditing && editingConfig?.has_webhook
                      ? "Leave blank to keep the existing webhook. Enter a new value to replace it."
                      : "Create an incoming webhook in your Slack workspace settings."
                  }
                />
              </Label>
              <Input
                id="webhook_url"
                type="password"
                  placeholder={
                    isEditing && editingConfig?.has_webhook
                      ? "••••••••••••••••"
                      : "https://hooks.slack.com/services/T.../B.../xxx"
                  }
                autoComplete="off"
                value={form.watch("webhook_url")}
                onChange={(event) => form.setValue("webhook_url", event.target.value)}
              />
              {form.formState.errors.webhook_url?.message ? (
                <p className="text-xs text-destructive" role="alert">{form.formState.errors.webhook_url.message}</p>
              ) : null}
            </div>

            <FormField
              name="channel"
              label="Channel override (optional)"
              description="Blank uses the webhook's channel."
            >
              {(field) => (
                <Input
                  placeholder="Leave blank for the webhook channel"
                  value={String(field.value ?? "")}
                  onChange={field.onChange}
                  onBlur={field.onBlur}
                  name={field.name}
                  ref={field.ref}
                />
              )}
            </FormField>

            <Button type="button" variant="secondary" loading={testConnection.isPending} onClick={() => testConnection.mutate()}>
              Send test message
            </Button>

            <Separator />
            <p className="text-sm font-medium">Daily Report Settings</p>

            <div className="flex items-center gap-2">
              <Switch
                id="daily_report_enabled"
                checked={form.watch("daily_report_enabled")}
                onCheckedChange={(checked) => form.setValue("daily_report_enabled", checked)}
              />
              <Label htmlFor="daily_report_enabled">Enable daily reports</Label>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="daily_report_cron">
                <HintLabel
                  label="Report schedule"
                  tooltip="Cron expression for when to send the daily report. Times are in UTC."
                />
              </Label>
              <CronInput
                value={form.watch("daily_report_cron")}
                onChange={(cron) => form.setValue("daily_report_cron", cron)}
                placeholder="0 9 * * *"
              />
              {form.formState.errors.daily_report_cron?.message ? (
                <p className="text-xs text-destructive" role="alert">{form.formState.errors.daily_report_cron.message}</p>
              ) : null}
            </div>

            <div className="flex items-center gap-2">
              <Switch
                id="daily_report_use_ai"
                checked={form.watch("daily_report_use_ai")}
                onCheckedChange={(checked) => form.setValue("daily_report_use_ai", checked)}
              />
              <Label htmlFor="daily_report_use_ai">
                <HintLabel
                  label="Include AI summary"
                  tooltip="Use the configured AI provider to generate an executive summary of the report."
                />
              </Label>
            </div>

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
                  tooltip="The default Slack connection is used for automated reports. Only one can be default."
                />
              </Label>
            </div>

            <Alert
              variant="info"
              title="How to create a Slack webhook"
              description={
                <ol className="m-0 list-inside list-decimal pl-0">
                  <li>Go to your Slack workspace settings</li>
                  <li>Navigate to Apps → Manage → Custom Integrations → Incoming Webhooks</li>
                  <li>Click &quot;Add to Slack&quot; and select the channel</li>
                  <li>Copy the webhook URL and paste it above</li>
                </ol>
              }
            />

            {(() => {
              const clearingChannel =
                isEditing && !form.watch("webhook_url").trim() && !form.watch("channel").trim();
              const canSave = testPassed || clearingChannel;
              return (
                <>
                  {!canSave ? (
                    <Alert
                      variant="warning"
                      title="Send a test message before saving"
                      description="Click &quot;Send test message&quot; above with a passing result to enable Save. Clearing the channel override does not need a new test."
                    />
                  ) : null}
                  <Button type="submit" loading={saving} disabled={!canSave}>
                    Save
                  </Button>
                </>
              );
            })()}
          </form>
        </FormProvider>
      </SheetContent>
    </Sheet>
  );
};
