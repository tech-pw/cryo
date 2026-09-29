import type { PartitionRead } from "../../api/types";
import { formatGb } from "../../lib/format";

interface PartitionHistogramProps {
  partitions: PartitionRead[];
}

const measured = (value: number | null | undefined): number | null =>
  value == null || Number.isNaN(value) ? null : value;

const sizeLabel = (value: number | null | undefined): string => {
  const size = measured(value);
  return size == null ? "Not measured" : formatGb(size);
};

const rateLabel = (small: number | null | undefined, files: number | null | undefined): string => {
  const smallCount = measured(small);
  const fileCount = measured(files);
  if (smallCount == null || fileCount == null || fileCount <= 0) return "Not measured";
  return `${Math.round((smallCount / fileCount) * 100)}%`;
};

const nameLabel = (value: string | null | undefined): string =>
  value == null || value === "" ? "Not measured" : value;

export const PartitionHistogram = ({ partitions }: PartitionHistogramProps) => {
  const ranked = partitions
    .map((item, index) => ({ item, index }))
    .sort((left, right) => {
      const leftSize = measured(left.item.partition_size_gb);
      const rightSize = measured(right.item.partition_size_gb);
      if (leftSize == null && rightSize == null) return left.index - right.index;
      if (leftSize == null) return 1;
      if (rightSize == null) return -1;
      return rightSize - leftSize || left.index - right.index;
    })
    .slice(0, 15);

  if (!partitions.length) {
    return <p className="text-sm text-muted-foreground">No partitions yet.</p>;
  }

  return (
    <div className="td-partition-histogram">
      <p className="mb-2 text-xs text-muted-foreground">
        Top {ranked.length} of {partitions.length} partitions
      </p>
      <table className="w-full text-sm">
        <caption className="sr-only">
          Top {ranked.length} of {partitions.length} partitions ranked by size
        </caption>
        <thead>
          <tr className="text-left text-xs text-muted-foreground">
            <th className="py-1 pr-3 font-medium">Partition</th>
            <th className="py-1 pr-3 text-right font-medium">Size</th>
            <th className="py-1 text-right font-medium">Small-file rate</th>
          </tr>
        </thead>
        <tbody>
          {ranked.map(({ item, index }) => (
            <tr key={`${item.partition_value ?? "partition"}-${index}`} className="border-t border-border/70">
              <td className="py-1.5 pr-3 break-words">{nameLabel(item.partition_value)}</td>
              <td className="py-1.5 pr-3 text-right [font-variant-numeric:tabular-nums]">
                {sizeLabel(item.partition_size_gb)}
              </td>
              <td className="py-1.5 text-right [font-variant-numeric:tabular-nums]">
                {rateLabel(item.small_files_in_partition_count, item.files_per_partition)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
