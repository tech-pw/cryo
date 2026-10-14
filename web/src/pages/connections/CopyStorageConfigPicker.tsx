import { useState } from "react";
import type { SourceRead } from "../../api/types";
import { Button } from "../../components/ui/button";
import { S3_AUTH_METHODS } from "./constants";
import { decomposeSourceConfig } from "./sourceConfig";

/**
 * Offered once a catalog test passes, before the operator fills in Storage from
 * scratch. Reuses another already-saved lakehouse's auth method, region, role ARN,
 * etc. — never its secret: decomposeSourceConfig always redacts
 * s3.secret-access-key, so an access-key source still requires the key to be
 * re-entered. That's deliberate, not a gap in this picker.
 */
export const CopyStorageConfigPicker = ({
  otherSources,
  onApply
}: {
  otherSources: SourceRead[];
  onApply: (config: ReturnType<typeof decomposeSourceConfig>) => void;
}) => {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  if (otherSources.length === 0) return null;

  const selected = otherSources.find((s) => s.id === selectedId) ?? null;
  const selectedConfig = selected ? decomposeSourceConfig(selected.config as Record<string, unknown>) : null;
  const authLabel = selectedConfig
    ? (S3_AUTH_METHODS.find((m) => m.value === selectedConfig.s3_auth_method)?.label ?? "Default")
    : null;

  return (
    <div className="rounded-[var(--radius-default)] border border-dashed border-border p-3">
      <p className="text-sm font-medium">Reuse storage auth from another lakehouse</p>
      <p className="mt-0.5 text-xs text-muted-foreground">
        Copies the auth method, region, and role ARN — never a saved secret key, which you'd re-enter either way.
      </p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {otherSources.map((source) => (
          <button
            key={source.id}
            type="button"
            onClick={() => setSelectedId((prev) => (prev === source.id ? null : source.id))}
            className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors duration-[var(--motion-fast)] ${
              selectedId === source.id
                ? "border-foreground bg-[var(--color-surface-sunken)] text-foreground"
                : "border-border text-muted-foreground hover:text-foreground"
            }`}
          >
            {source.name}
          </button>
        ))}
      </div>
      {selected && selectedConfig ? (
        <div className="mt-2.5 flex items-center justify-between gap-3 rounded-[var(--radius-default)] bg-[var(--color-surface-sunken)] px-2.5 py-2">
          <p className="text-xs text-muted-foreground">
            {authLabel}
            {selectedConfig.s3_region ? ` · ${selectedConfig.s3_region}` : ""}
          </p>
          <Button type="button" size="sm" variant="secondary" onClick={() => onApply(selectedConfig)}>
            Use this
          </Button>
        </div>
      ) : null}
    </div>
  );
};
