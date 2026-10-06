import { useFormContext } from "react-hook-form";
import { Alert } from "./ui/alert";
import { FormField } from "./ui/form";
import { Input } from "./ui/input";
import { InputNumber } from "./ui/input-number";
import { Label } from "./ui/label";
import { RadioGroup } from "./ui/radio-group";
import { Switch } from "./ui/switch";
import { Textarea } from "./ui/textarea";
import type { DestinationFormValues } from "../pages/connections/destinationConfig";

export interface QueryLogIngestionFieldsProps {
  /** When the engine is Spark EMR, ingestion is unsupported and the section collapses. */
  engine: "trino" | "spark_emr";
}

/**
 * Sub-form for the per-destination query-log ingestion block. Lives inside the Destination
 * drawer on Connections. Sub-form values live on flat ``query_log_*`` form fields; the
 * parent submit handler merges them into the ``query_log_config`` JSONB block sent
 * to the backend.
 */
export const QueryLogIngestionFields = ({ engine }: QueryLogIngestionFieldsProps) => {
  const form = useFormContext<DestinationFormValues>();
  const enabled = form.watch("query_log_enabled");
  const mode = form.watch("query_log_mode");
  const retentionDays = form.watch("query_log_retention_days");

  if (engine !== "trino") {
    return (
      <Alert
        variant="info"
        title="Query-log ingestion is Trino-only in this build."
        description="Spark history ingestion lands separately — this destination's maintenance path doesn't need it."
      />
    );
  }

  return (
    <div className="mt-3 rounded-[var(--radius-default)] border border-border p-3">
      <div className="space-y-3">
        <p className="text-sm font-medium">Query log ingestion</p>
        <p className="text-sm text-muted-foreground">
          Stream this destination&apos;s queries into the Cryo archive so the Optimize page
          (layout recommendations, partition evolution) and the Governor (variance-aware QPP) have
          real traffic to learn from. Without this, recommendations can only be derived from manifest
          metadata, not from what users actually run.
        </p>

        <div className="flex items-center gap-2">
          <Switch
            id="query_log_enabled"
            checked={Boolean(enabled)}
            onCheckedChange={(checked) => form.setValue("query_log_enabled", checked)}
          />
          <Label htmlFor="query_log_enabled">Enable query log ingestion</Label>
        </div>

        {enabled ? (
          <>
            <FormField name="query_log_mode" label="Mode">
              {(field) => (
                <RadioGroup
                  name={field.name}
                  value={String(field.value ?? "trino_system_runtime")}
                  onChange={field.onChange}
                  options={[
                    {
                      value: "trino_system_runtime",
                      label: "Poll Trino system.runtime.queries",
                      description:
                        "Curator pulls recent queries from one or more Trino coordinators and archives them. Recommended for most deployments."
                    },
                    {
                      value: "read_through",
                      label: "Read from existing Iceberg query log",
                      description:
                        "You already run a Trino event listener that writes every completed query to an Iceberg table. Cryo reads that table directly; nothing extra to archive. Pick this if you want full control over retention."
                    }
                  ]}
                />
              )}
            </FormField>

            {mode === "read_through" ? (
              <FormField
                name="query_log_external_table"
                label="External Iceberg table (namespace.table)"
                description="Must be readable via this destination's catalog connection. Schema: needs a query text column and a timestamp column."
              >
                {(field) => (
                  <Input
                    placeholder="logging.trino_query_log"
                    value={String(field.value ?? "")}
                    onChange={field.onChange}
                    onBlur={field.onBlur}
                    name={field.name}
                    ref={field.ref}
                  />
                )}
              </FormField>
            ) : null}

            {mode === "trino_system_runtime" ? (
              <>
                <FormField
                  name="query_log_telemetry_coordinators"
                  label="Telemetry coordinators (one host:port per line)"
                  description="Leave blank to use the destination's primary coordinator. List every active coordinator for multi-coordinator clusters — the curator will poll every one and dedupe by query_id."
                >
                  {(field) => (
                    <Textarea
                      rows={3}
                      placeholder={"http://trino-coord-1:8080\nhttp://trino-coord-2:8080"}
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
                    name="query_log_pull_interval_seconds"
                    label="Pull interval (seconds)"
                    description="How often the curator polls Trino. Below 1-day retention this is auto-set to 60s."
                  >
                    {(field) => (
                      <InputNumber
                        min={10}
                        max={3600}
                        value={field.value as number | null | undefined}
                        onChange={field.onChange}
                        onBlur={field.onBlur}
                        name={field.name}
                        ref={field.ref}
                      />
                    )}
                  </FormField>
                  <FormField
                    name="query_log_watermark_overlap_seconds"
                    label="Overlap (seconds)"
                    description="How far back the curator re-asks Trino for queries. Must be wider than Trino's query.min-expire-age (default 15 min) so a missed tick never loses data."
                  >
                    {(field) => (
                      <InputNumber
                        min={300}
                        max={86400}
                        value={field.value as number | null | undefined}
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

            <FormField
              name="query_log_sample_rate"
              label="Archive sample rate (0.0 - 1.0)"
              description="Deterministic per query_id. 1.0 = archive every query; 0.01 = sample 1%. Apply when the destination is too chatty to keep up with full archival."
            >
              {(field) => (
                <InputNumber
                  min={0}
                  max={1}
                  step={0.05}
                  value={field.value as number | null | undefined}
                  onChange={field.onChange}
                  onBlur={field.onBlur}
                  name={field.name}
                  ref={field.ref}
                />
              )}
            </FormField>

            <FormField
              name="query_log_retention_days"
              label="Iceberg retention (days, 1-365)"
              description="Snapshots older than this are expired on every curator tick. NULL = no auto-expiry (you manage retention)."
            >
              {(field) => (
                <InputNumber
                  min={1}
                  max={365}
                  value={field.value as number | null | undefined}
                  onChange={field.onChange}
                  onBlur={field.onBlur}
                  name={field.name}
                  ref={field.ref}
                />
              )}
            </FormField>

            {typeof retentionDays === "number" && retentionDays <= 1 ? (
              <Alert
                variant="warning"
                title="Very short retention"
                description="At ≤ 1 day, the curator pulls every 60s with a 5-min overlap. A missed curator tick (process restart, network blip) means lost query data. Don't go below 1 day unless you also have a Trino event listener as a backup."
              />
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
};
