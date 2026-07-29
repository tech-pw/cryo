import { Copy } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { toast } from "sonner";
import {
  createAwsIntegration,
  deleteAwsIntegration,
  getAwsIntegration,
  getAwsUsage,
  patchAwsIntegration,
  testAwsIntegration
} from "../api/client";
import type { AWSIntegrationRead, AWSIntegrationWrite, UUID } from "../api/types";
import { ConfirmAction } from "./confirm";
import { HintLabel } from "./common";
import { Alert } from "./ui/alert";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { FormField, FormProvider, useForm } from "./ui/form";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Select } from "./ui/select";
import { Switch } from "./ui/switch";
import { useAuth } from "../lib/auth";
import { formatBytesGb, formatCurrency, formatTime } from "../lib/format";

interface AwsIntegrationPanelProps {
  sourceId: UUID;
  /** When true, render in a denser card layout (used inside a grid of integrations). */
  compact?: boolean;
}

const AWS_REGIONS = [
  "us-east-1",
  "us-east-2",
  "us-west-1",
  "us-west-2",
  "af-south-1",
  "ap-east-1",
  "ap-south-1",
  "ap-south-2",
  "ap-southeast-1",
  "ap-southeast-2",
  "ap-southeast-3",
  "ap-southeast-4",
  "ap-northeast-1",
  "ap-northeast-2",
  "ap-northeast-3",
  "ca-central-1",
  "eu-central-1",
  "eu-central-2",
  "eu-west-1",
  "eu-west-2",
  "eu-west-3",
  "eu-north-1",
  "eu-south-1",
  "eu-south-2",
  "me-central-1",
  "me-south-1",
  "sa-east-1"
].map((region) => ({ value: region, label: region }));

type AwsFormValues = AWSIntegrationWrite & { secret_access_key?: string };

const formatRelative = (value?: string | null): string => {
  if (!value) return "Never";
  const ts = new Date(value).getTime();
  if (Number.isNaN(ts)) return "Never";
  const deltaSec = Math.round((Date.now() - ts) / 1000);
  if (deltaSec < 60) return `${deltaSec}s ago`;
  const min = Math.round(deltaSec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  return `${day}d ago`;
};

export const AwsIntegrationPanel = ({ sourceId, compact = false }: AwsIntegrationPanelProps) => {
  const { api } = useAuth();
  const queryClient = useQueryClient();

  const form = useForm<AwsFormValues>({
    defaultValues: {
      bucket: "",
      region: "us-east-1",
      access_key_id: "",
      enabled: true
    }
  });

  const integration = useQuery({
    queryKey: ["aws-integration", sourceId],
    queryFn: async () => {
      try {
        return await getAwsIntegration(api, sourceId);
      } catch (error) {
        if (error instanceof Error && error.message.includes("404")) return null;
        throw error;
      }
    },
    retry: false
  });

  const existing: AWSIntegrationRead | undefined = integration.data ?? undefined;

  useEffect(() => {
    form.reset({
      bucket: existing?.bucket ?? "",
      region: existing?.region ?? "us-east-1",
      access_key_id: existing?.access_key_id ?? "",
      enabled: existing?.enabled ?? true,
      secret_access_key: ""
    });
  }, [existing?.id, existing?.bucket, existing?.region, existing?.access_key_id, existing?.enabled, form]);

  const usage = useQuery({
    queryKey: ["aws-usage", sourceId],
    queryFn: () => getAwsUsage(api, sourceId),
    enabled: Boolean(integration.data?.enabled),
    retry: false,
    refetchInterval: 60_000
  });

  const save = useMutation({
    mutationFn: (values: AwsFormValues) => {
      const body: AWSIntegrationWrite = {
        bucket: values.bucket,
        region: values.region,
        access_key_id: values.access_key_id || undefined,
        enabled: values.enabled
      };
      if (values.secret_access_key) {
        body.secret_access_key = values.secret_access_key;
      }
      if (integration.data) {
        return patchAwsIntegration(api, sourceId, body);
      }
      return createAwsIntegration(api, sourceId, body);
    },
    onSuccess: () => {
      toast.success(integration.data ? "AWS integration updated" : "AWS integration connected");
      void queryClient.invalidateQueries({ queryKey: ["aws-integration", sourceId] });
      void queryClient.invalidateQueries({ queryKey: ["aws-integrations-overview"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const remove = useMutation({
    mutationFn: () => deleteAwsIntegration(api, sourceId),
    onSuccess: () => {
      toast.success("AWS integration removed");
      form.reset();
      void queryClient.invalidateQueries({ queryKey: ["aws-integration", sourceId] });
      void queryClient.invalidateQueries({ queryKey: ["aws-integrations-overview"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const test = useMutation({
    mutationFn: () => testAwsIntegration(api, sourceId),
    onSuccess: (result) => {
      if (result.ok) {
        toast.success(`Connected to AWS account ${result.account_id ?? "?"}`);
      } else {
        toast.error(result.error ?? "Connection failed");
      }
      void queryClient.invalidateQueries({ queryKey: ["aws-integration", sourceId] });
      void queryClient.invalidateQueries({ queryKey: ["aws-integrations-overview"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  if (integration.isError && !integration.isLoading) {
    const status = (integration.error as { status?: number } | null)?.status;
    if (status === 404) {
      return (
        <p className="text-sm text-muted-foreground">
          Connect this lakehouse&apos;s S3 bucket to Cryo so we can pull free CloudWatch storage metrics
          and real regional pricing — no extra AWS charges.
        </p>
      );
    }
  }

  const submit = (values: AwsFormValues) => {
    if (!values.bucket.trim()) {
      form.setError("bucket", { message: "Bucket name is required" });
      return;
    }
    if (!values.region?.trim()) {
      form.setError("region", { message: "Region is required" });
      return;
    }
    save.mutate(values);
  };

  return (
    <div className="flex flex-col gap-3">
      {!compact ? (
        <Alert
          variant="info"
          title="Free-tier AWS metrics only"
          description="Uses S3 CloudWatch daily storage metrics and the Price List API — no LIST, no charges."
        />
      ) : null}

      <FormProvider {...form}>
        <form className="flex flex-col gap-3" onSubmit={form.handleSubmit(submit)}>
          <FormField name="bucket" label="S3 bucket">
            {(field) => (
              <Input
                placeholder="my-iceberg-bucket"
                autoComplete="off"
                value={String(field.value ?? "")}
                onChange={field.onChange}
                onBlur={field.onBlur}
                name={field.name}
                ref={field.ref}
              />
            )}
          </FormField>

          <FormField name="region" label="Region">
            {(field) => (
              <Select
                value={String(field.value ?? "")}
                onChange={(next) => field.onChange(next ?? "")}
                options={AWS_REGIONS}
                placeholder="ap-south-1"
                onBlur={field.onBlur}
                name={field.name}
                ref={field.ref}
              />
            )}
          </FormField>

          <FormField
            name="access_key_id"
            label={
              <HintLabel
                label="Access key ID (optional)"
                tooltip="Leave blank to use IRSA, instance profile, or ~/.aws/credentials."
              />
            }
          >
            {(field) => (
              <Input
                placeholder="AKIA..."
                autoComplete="off"
                value={String(field.value ?? "")}
                onChange={field.onChange}
                onBlur={field.onBlur}
                name={field.name}
                ref={field.ref}
              />
            )}
          </FormField>

          <FormField
            name="secret_access_key"
            label={
              <HintLabel
                label="Secret access key"
                tooltip={
                  existing
                    ? "Stored encrypted at rest. Leave blank to keep the existing secret."
                    : "Stored encrypted at rest. Leave blank to use the default AWS credential chain."
                }
              />
            }
          >
            {(field) => (
              <Input
                type="password"
                placeholder={existing ? "Leave blank to keep existing" : "Optional with IRSA"}
                autoComplete="off"
                value={String(field.value ?? "")}
                onChange={field.onChange}
                onBlur={field.onBlur}
                name={field.name}
                ref={field.ref}
              />
            )}
          </FormField>

          <div className="flex items-center gap-2">
            <Switch
              id={`aws-enabled-${sourceId}`}
              checked={form.watch("enabled")}
              onCheckedChange={(checked) => form.setValue("enabled", checked)}
            />
            <Label htmlFor={`aws-enabled-${sourceId}`}>Enabled</Label>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button type="submit" loading={save.isPending}>
              {existing ? "Save changes" : "Connect"}
            </Button>
            {existing ? (
              <>
                <Button type="button" variant="secondary" loading={test.isPending} onClick={() => test.mutate()}>
                  Test connection
                </Button>
                <ConfirmAction
                  title="Remove AWS integration?"
                  description="CloudWatch cost metrics for this source will stop updating. You can reconnect any time."
                  onConfirm={() => remove.mutate()}
                  okText="Remove"
                >
                  <Button type="button" variant="danger" loading={remove.isPending}>
                    Remove
                  </Button>
                </ConfirmAction>
              </>
            ) : null}
          </div>
        </form>
      </FormProvider>

      {existing ? (
        <div className="flex flex-wrap items-center gap-4">
          <Badge variant={existing.enabled ? "success" : "secondary"}>
            {existing.enabled ? "Enabled" : "Disabled"}
          </Badge>
          <span className="text-sm text-muted-foreground">
            Last tested: {formatRelative(existing.last_tested_at)}
            {existing.last_tested_at ? ` (${formatTime(existing.last_tested_at)})` : ""}
          </span>
          {existing.last_test_ok === true ? <Badge variant="success">OK</Badge> : null}
          {existing.last_test_ok === false ? <Badge variant="danger">Failed</Badge> : null}
        </div>
      ) : null}

      {test.data?.ok && test.data.arn ? (
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">{test.data.arn}</span>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            aria-label="Copy ARN"
            onClick={() => {
              void navigator.clipboard.writeText(test.data?.arn ?? "").then(
                () => toast.success("ARN copied"),
                () => toast.error("Could not copy to clipboard")
              );
            }}
          >
            <Copy className="size-3.5" aria-hidden="true" />
          </Button>
        </div>
      ) : null}

      {existing?.enabled ? (
        usage.isLoading ? (
          <p className="text-sm text-muted-foreground">Loading bucket metrics…</p>
        ) : usage.data ? (
          <div className="flex flex-wrap gap-6">
            <div>
              <p className="text-xs text-muted-foreground">Bucket size</p>
              <p className="text-base font-semibold">{formatBytesGb(usage.data.total_size_bytes)}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Objects</p>
              <p className="text-base font-semibold">{usage.data.object_count?.toLocaleString() ?? "—"}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">$/GB-month</p>
              <p className="text-base font-semibold">
                {usage.data.price_per_gb_month != null ? formatCurrency(usage.data.price_per_gb_month) : "—"}
                <span className="ml-1 text-xs font-normal text-muted-foreground">
                  {usage.data.price_source ?? "AWS"}
                </span>
              </p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Measured</p>
              <p className="text-base font-semibold">
                {usage.data.measured_at ? formatTime(usage.data.measured_at) : "—"}
              </p>
            </div>
          </div>
        ) : usage.isError ? (
          <p className="text-sm text-muted-foreground">
            Usage metrics unavailable — check credentials and bucket access.
          </p>
        ) : null
      ) : null}
    </div>
  );
};
