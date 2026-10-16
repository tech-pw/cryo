import type { DestinationRead, SourceRead } from "../../api/types";
import { CATALOG_TYPES } from "./constants";
import { decomposeDestinationConfig } from "./destinationConfig";
import { decomposeSourceConfig } from "./sourceConfig";

export interface ConnectionDetail {
  label: string;
  value: string;
  mono?: boolean;
}

const S3_AUTH_LABEL = {
  default: "Default credentials",
  access_key: "Static access keys",
  assume_role: "Assume IAM role"
} as const;

/** Drops `user:password@` so a URI with embedded credentials never renders. */
export const redactUri = (value: string): string => value.replace(/^([a-z][a-z0-9+.-]*:\/\/)[^/@\s]*@/i, "$1");

export const catalogTypeLabel = (type: string): string =>
  CATALOG_TYPES.find((option) => option.value === type)?.label ?? type;

export const sourceDetails = (source: SourceRead): ConnectionDetail[] => {
  const cfg = decomposeSourceConfig(source.config);
  const details: ConnectionDetail[] = [];
  if (cfg.uri) details.push({ label: "Endpoint", value: redactUri(cfg.uri), mono: true });
  if (cfg.warehouse) details.push({ label: "Warehouse", value: redactUri(cfg.warehouse), mono: true });
  if (cfg.s3_region) details.push({ label: "Region", value: cfg.s3_region });
  details.push({ label: "S3 auth", value: S3_AUTH_LABEL[cfg.s3_auth_method] });
  return details;
};

const formatHour = (hour: number) => `${String(hour).padStart(2, "0")}:00`;

export const offpeakLabel = (destination: Pick<DestinationRead, "offpeak_start" | "offpeak_end">): string => {
  const { offpeak_start: start, offpeak_end: end } = destination;
  if (start == null || end == null || start === end) return "Any time";
  return `${formatHour(start)}–${formatHour(end)} IST`;
};

/** Engines that execute inside the Cryo deployment (native subprocess, Spark-on-Kubernetes dispatch). */
export const isCryoEngine = (destination: Pick<DestinationRead, "engine" | "config">): boolean =>
  destination.engine === "native" ||
  (destination.engine === "spark_emr" && destination.config?.submit_mode === "k8s");

export const cryoEngineCaveat = (destination: Pick<DestinationRead, "engine">): string =>
  destination.engine === "native"
    ? "Runs as a subprocess on Cryo's own workers. Slot cap and timeouts here bound every job routed to it, and presets that target this engine pick up the change on their next run."
    : "Runs on the Spark-on-Kubernetes dispatch shipped with Cryo. The nightly batch needs exactly one enabled Spark engine, so disabling this one, or enabling a second, fails the batch until it is resolved.";

export const destinationDetails = (destination: DestinationRead): ConnectionDetail[] => {
  const cfg = decomposeDestinationConfig(destination.engine, destination.config) as Record<string, unknown>;
  const details: ConnectionDetail[] = [];
  const raw = destination.config ?? {};
  if (destination.engine === "trino") {
    const coordinators = Array.isArray(raw.coordinators)
      ? (cfg.coordinators as { host: string; port: number }[])
      : [];
    const first = coordinators[0];
    if (first?.host) {
      const more = coordinators.length > 1 ? ` +${coordinators.length - 1}` : "";
      details.push({ label: "Coordinator", value: `${first.host}:${first.port}${more}`, mono: true });
    }
    if (typeof raw.catalog === "string" && raw.catalog) details.push({ label: "Catalog", value: raw.catalog, mono: true });
  } else if (destination.engine === "spark_emr") {
    if (typeof raw.application_id === "string" && raw.application_id) {
      details.push({ label: "Application", value: raw.application_id, mono: true });
    }
    if (typeof raw.region === "string" && raw.region) details.push({ label: "Region", value: raw.region });
  } else if (destination.engine === "native") {
    if (typeof raw.slot_cap === "number") details.push({ label: "Slot cap", value: String(raw.slot_cap) });
  }
  details.push({ label: "Off-peak", value: offpeakLabel(destination) });
  return details;
};
