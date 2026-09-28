import { formatBytesGb } from "../../lib/format";

interface StorageDonutProps {
  liveBytes: number | null | undefined;
  deadBytes: number | null | undefined;
  metadataBytes: number | null | undefined;
  orphanBytes?: number | null;
  pricePerGbMonth?: number;
}

const measuredBytes = (value: number | null | undefined): number | null =>
  value != null && Number.isFinite(value) ? value : null;

const byteLabel = (value: number | null): string => (value == null ? "Not measured" : formatBytesGb(value));

export const StorageDonut = ({
  liveBytes,
  deadBytes,
  metadataBytes,
  orphanBytes
}: StorageDonutProps) => {
  const parts = [
    { key: "Data", bytes: measuredBytes(liveBytes) },
    { key: "Delete", bytes: measuredBytes(deadBytes) },
    { key: "Metadata", bytes: measuredBytes(metadataBytes) },
    { key: "Orphan", bytes: measuredBytes(orphanBytes) }
  ];
  const measured = parts.filter((part) => part.bytes != null).length;

  return (
    <div className="td-storage-measures">
      <p className="mb-2 text-xs text-muted-foreground">
        {measured} of {parts.length} measured
      </p>
      <dl className="m-0 grid gap-1 text-sm">
        {parts.map((part) => (
          <div key={part.key} className="flex items-baseline justify-between gap-3">
            <dt className="font-medium">{part.key}</dt>
            <dd className="m-0 tabular-nums">{byteLabel(part.bytes)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
};
