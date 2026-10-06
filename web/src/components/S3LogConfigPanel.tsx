import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  checkS3LogConfigLogging,
  ingestS3LogConfig,
  listS3LogConfigs,
  patchS3LogConfig,
  probeS3LogConfigTarget,
  provisionS3LogConfig,
  teardownS3LogConfig
} from "../api/client";
import type { S3LogConfigRead, UUID } from "../api/types";
import { ConfirmAction } from "./confirm";
import { useAuth } from "../lib/auth";
import { formatTime } from "../lib/format";
import { Alert } from "./ui/alert";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { FormField, FormProvider, useForm } from "./ui/form";
import { Input } from "./ui/input";
import { Switch } from "./ui/switch";

interface S3LogConfigPanelProps {
  sourceId: UUID;
}

interface ManualFormValues {
  log_target_bucket?: string;
  log_target_prefix?: string;
  access_key_id?: string;
  secret_access_key?: string;
  region?: string;
}

const BucketCard = ({ sourceId, config }: { sourceId: UUID; config: S3LogConfigRead }) => {
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const [probeResult, setProbeResult] = useState<string | null>(null);

  const form = useForm<ManualFormValues>({
    defaultValues: {
      log_target_bucket: config.log_target_bucket ?? "",
      log_target_prefix: config.log_target_prefix ?? "",
      access_key_id: config.access_key_id ?? "",
      region: config.region ?? "",
      secret_access_key: ""
    }
  });

  useEffect(() => {
    form.reset({
      log_target_bucket: config.log_target_bucket ?? "",
      log_target_prefix: config.log_target_prefix ?? "",
      access_key_id: config.access_key_id ?? "",
      region: config.region ?? "",
      secret_access_key: ""
    });
  }, [
    config.log_target_bucket,
    config.log_target_prefix,
    config.access_key_id,
    config.region,
    form
  ]);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["s3-log-configs", sourceId] });

  const check = useMutation({
    mutationFn: () => checkS3LogConfigLogging(api, sourceId, config.bucket),
    onSuccess: (result) => {
      if (result.error) toast.error(result.error);
      else toast.success(`Logging status: ${result.logging_status}`);
      invalidate();
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const saveManual = useMutation({
    mutationFn: (values: ManualFormValues) =>
      patchS3LogConfig(api, sourceId, config.bucket, {
        log_target_bucket: values.log_target_bucket || null,
        log_target_prefix: values.log_target_prefix || null,
        access_key_id: values.access_key_id || null,
        secret_access_key: values.secret_access_key || undefined,
        region: values.region || null,
        manual_override: true
      }),
    onSuccess: () => {
      toast.success("Log target saved");
      invalidate();
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const probe = useMutation({
    mutationFn: () => probeS3LogConfigTarget(api, sourceId, config.bucket),
    onSuccess: (result) => {
      if (result.error) setProbeResult(`Could not check this path: ${result.error}`);
      else if (result.files_found > 0)
        setProbeResult(`Found ${result.files_found}${result.truncated ? "+" : ""} log files at this path`);
      else setProbeResult("Nothing found yet — double check the path, or wait for logs to arrive");
    },
    onError: (error) => setProbeResult((error as Error).message)
  });

  const toggleEnabled = useMutation({
    mutationFn: (enabled: boolean) => patchS3LogConfig(api, sourceId, config.bucket, { enabled }),
    onSuccess: invalidate,
    onError: (error) => toast.error((error as Error).message)
  });

  const ingestNow = useMutation({
    mutationFn: () => ingestS3LogConfig(api, sourceId, config.bucket),
    onSuccess: (result) => {
      if (result.error) toast.error(result.error);
      else toast.success(`Ingested ${result.rows_ingested} row(s), matched ${result.tables_matched} table(s)`);
      invalidate();
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const enableLogging = useMutation({
    mutationFn: () => provisionS3LogConfig(api, sourceId, config.bucket),
    onSuccess: (result) => {
      if (result.ok) toast.success(`Logging enabled, delivering to ${result.log_target_bucket}`);
      else toast.error(result.error ?? "Could not enable logging");
      invalidate();
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const removeTracking = useMutation({
    mutationFn: () => teardownS3LogConfig(api, sourceId, config.bucket, { revert_policy: true }),
    onSuccess: (result) => {
      if (result.warnings.length) toast.warning(result.warnings.join("; "));
      else toast.success("Bucket tracking removed");
      invalidate();
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const hasTarget = Boolean(config.log_target_bucket);
  const awaitingFirstDelivery = hasTarget && config.enabled && !config.last_ingested_at;
  const loggingVariant =
    config.logging_status === "enabled" ? "success" : config.logging_status === "disabled" ? "warning" : "secondary";

  return (
    <Card className="mb-3">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm">{config.bucket}</CardTitle>
        <div className="flex items-center gap-2">
          <Badge variant={loggingVariant}>{config.logging_status}</Badge>
          <Switch
            checked={config.enabled}
            disabled={!hasTarget || toggleEnabled.isPending}
            onCheckedChange={(checked) => toggleEnabled.mutate(checked)}
          />
        </div>
      </CardHeader>
      <CardContent className="space-y-2.5">
        <div className="flex flex-wrap gap-2">
          <Button size="sm" loading={check.isPending} onClick={() => check.mutate()}>
            Check logging status
          </Button>
          {config.enabled && hasTarget ? (
            <Button size="sm" loading={ingestNow.isPending} onClick={() => ingestNow.mutate()}>
              Ingest now
            </Button>
          ) : null}
          <ConfirmAction
            title="Remove tracking for this bucket?"
            description="Removes Cryo's config for this bucket and may revert the bucket-policy statement Cryo added."
            onConfirm={() => removeTracking.mutate()}
            okText="Remove"
          >
            <Button size="sm" variant="danger" loading={removeTracking.isPending}>
              Remove tracking
            </Button>
          </ConfirmAction>
        </div>

        {config.last_error ? (
          <Alert variant="error" title="Last check failed" description={config.last_error} />
        ) : null}

        {config.logging_status === "enabled" && hasTarget ? (
          <p className="text-xs text-muted-foreground">
            Logging target: {config.log_target_bucket}/{config.log_target_prefix ?? ""}
            {config.last_ingested_date ? ` — last ingested ${config.last_ingested_date}` : ""}
          </p>
        ) : null}

        {awaitingFirstDelivery ? (
          <Alert
            variant="info"
            title="Logging configured, waiting for first delivery"
            description="This usually takes a few hours after logging is turned on."
          />
        ) : null}

        {config.logging_status !== "enabled" ? (
          <>
            <p className="text-xs text-muted-foreground">
              Cryo can see request activity for this bucket once server access logging is turned on and pointed at a log
              target it can read.
            </p>
            <ConfirmAction
              title="Turn on request-cost logging for this bucket?"
              description={`Replaces any existing server-access-logging target for "${config.bucket}" with Cryo's bucket and prefix.`}
              onConfirm={() => enableLogging.mutate()}
              okText="Enable logging"
            >
              <Button size="sm" variant="primary" loading={enableLogging.isPending}>
                Enable logging
              </Button>
            </ConfirmAction>
            <FormProvider {...form}>
              <form className="space-y-3" onSubmit={form.handleSubmit((values) => saveManual.mutate(values))}>
                <div className="grid gap-3 sm:grid-cols-2">
                  <FormField name="log_target_bucket" label="Log target bucket">
                    {(field) => (
                      <Input
                        placeholder="my-access-logs"
                        autoComplete="off"
                        value={String(field.value ?? "")}
                        onChange={field.onChange}
                        onBlur={field.onBlur}
                        name={field.name}
                        ref={field.ref}
                      />
                    )}
                  </FormField>
                  <FormField name="log_target_prefix" label="Log target prefix">
                    {(field) => (
                      <Input
                        placeholder="logs/my-bucket/"
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
                      <Input
                        placeholder="us-east-1"
                        autoComplete="off"
                        value={String(field.value ?? "")}
                        onChange={field.onChange}
                        onBlur={field.onBlur}
                        name={field.name}
                        ref={field.ref}
                      />
                    )}
                  </FormField>
                  <FormField name="access_key_id" label="Access key ID (optional)">
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
                </div>
                <FormField name="secret_access_key" label="Secret access key (optional, stored encrypted)">
                  {(field) => (
                    <Input
                      type="password"
                      placeholder="Leave blank to keep existing"
                      autoComplete="off"
                      value={String(field.value ?? "")}
                      onChange={field.onChange}
                      onBlur={field.onBlur}
                      name={field.name}
                      ref={field.ref}
                    />
                  )}
                </FormField>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="primary" type="submit" loading={saveManual.isPending}>
                    Save target
                  </Button>
                  <Button size="sm" type="button" loading={probe.isPending} onClick={() => probe.mutate()}>
                    Validate path
                  </Button>
                </div>
              </form>
            </FormProvider>
            {probeResult ? <p className="text-xs text-muted-foreground">{probeResult}</p> : null}
          </>
        ) : null}
      </CardContent>
    </Card>
  );
};

export const S3LogConfigPanel = ({ sourceId }: S3LogConfigPanelProps) => {
  const { api } = useAuth();
  const configs = useQuery({
    queryKey: ["s3-log-configs", sourceId],
    queryFn: () => listS3LogConfigs(api, sourceId),
    retry: false
  });

  if (configs.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading buckets…</p>;
  }

  if (!configs.data || configs.data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No buckets discovered yet — click &quot;Run full cost scan&quot; on the source above so Cryo can record each
        table&apos;s S3 location.
      </p>
    );
  }

  return (
    <div className="w-full space-y-0">
      <p className="mb-3 text-[12.5px] text-muted-foreground">
        Turn this on to see per-table GET/PUT/LIST request costs, not just storage costs.
      </p>
      {configs.data.map((config) => (
        <BucketCard key={config.bucket} sourceId={sourceId} config={config} />
      ))}
    </div>
  );
};
