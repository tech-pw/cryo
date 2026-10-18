import type { TableStatsRead } from "../../../api/types";
import { formatNumber } from "../../../lib/format";

/** Iceberg's commit.manifest.target-size-bytes default; rewrite manifests packs toward it. */
export const MANIFEST_TARGET_BYTES = 8 * 1024 * 1024;
const PREVIOUS_VERSIONS_DEFAULT = 100;
/** cryo/scanner/health.py manifest_count_warn. */
const MANIFEST_COUNT_WARN = 100;

type Tone = "ok" | "warn" | "muted";

interface Fact {
  label: string;
  value: string;
  hint: string;
  tone: Tone;
}

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};

const num = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);

const mb = (bytes: number) => `${(bytes / 1024 / 1024).toFixed(bytes < 1024 * 1024 ? 2 : 1)} MB`;

export const manifestFacts = (stats: TableStatsRead | null | undefined): Fact[] => {
  const raw = record(stats?.raw);
  const shape = record(raw.manifest_stats);
  const props = record(raw.iceberg_properties);
  const count = num(shape.manifest_count) ?? num(stats?.manifest_count);
  if (count == null) return [];
  const avgBytes = num(shape.avg_manifest_bytes);
  const perManifest = num(shape.avg_files_per_manifest);
  const errors = Array.isArray(raw.manifest_read_errors) ? raw.manifest_read_errors.length : 0;
  const keepRaw = props["write.metadata.previous-versions-max"];
  const keep = typeof keepRaw === "string" && keepRaw.trim() !== "" ? Number(keepRaw) : null;
  const autoDelete = String(props["write.metadata.delete-after-commit.enabled"] ?? "").toLowerCase() === "true";

  const facts: Fact[] = [
    {
      label: "Manifests",
      value: formatNumber(count),
      hint:
        count >= MANIFEST_COUNT_WARN
          ? `At or above the health check's ${MANIFEST_COUNT_WARN}. Every query plan opens these before reading data.`
          : "Every query plan opens these before reading data.",
      tone: count >= MANIFEST_COUNT_WARN ? "warn" : "ok"
    }
  ];
  if (avgBytes != null && count > 1) {
    const small = avgBytes < MANIFEST_TARGET_BYTES / 4;
    facts.push({
      label: "Average manifest",
      value: mb(avgBytes),
      hint: small
        ? `Well under the ${mb(MANIFEST_TARGET_BYTES)} target. Rewrite manifests would merge them.`
        : `Target is ${mb(MANIFEST_TARGET_BYTES)}.`,
      tone: small ? "warn" : "ok"
    });
  }
  if (perManifest != null) {
    facts.push({
      label: "Files per manifest",
      value: formatNumber(Math.round(perManifest)),
      hint: "Low numbers mean many commits each wrote their own manifest.",
      tone: perManifest < 10 && count > 50 ? "warn" : "ok"
    });
  }
  facts.push({
    label: "Old metadata.json files",
    value: autoDelete ? `Keeps ${formatNumber(keep ?? PREVIOUS_VERSIONS_DEFAULT)}` : "Never deleted",
    hint: autoDelete
      ? "write.metadata.delete-after-commit is on, so each commit trims older versions."
      : `write.metadata.delete-after-commit is off${keep == null ? " (Iceberg's default)" : ""}, so every commit leaves one behind until orphan cleanup.`,
    tone: autoDelete ? "ok" : "warn"
  });
  if (errors) {
    facts.push({
      label: "Unreadable manifests",
      value: formatNumber(errors),
      hint: "The last scan couldn't read these, so its file counts may be low.",
      tone: "warn"
    });
  }
  return facts;
};

/** Manifest and metadata-file shape from the latest scan. */
export const ManifestHealth = ({ stats }: { stats: TableStatsRead | null | undefined }) => {
  const facts = manifestFacts(stats);
  if (!facts.length) return null;
  return (
    <section className="td-manifest" aria-labelledby="overview-manifests">
      <h3 id="overview-manifests" className="td-history-subhead">Manifests &amp; metadata</h3>
      <dl className="td-manifest-strip">
        {facts.map((fact) => (
          <div key={fact.label} data-tone={fact.tone}>
            <dt>{fact.label}</dt>
            <dd>{fact.value}</dd>
            <p>{fact.hint}</p>
          </div>
        ))}
      </dl>
    </section>
  );
};
