import { useQuery } from "@tanstack/react-query";

import { getPartitionEvolutionRecommendation, type PartitionEvolutionTrigger } from "../../../api/client";
import { useAuth } from "../../../lib/auth";
import { formatNumber } from "../../../lib/format";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import { Badge } from "../../ui/badge";

const TRIGGER: Record<PartitionEvolutionTrigger, string> = {
  filter_frequency: "Queries filter on a column the table isn't partitioned by",
  scan_ratio: "Queries read far more of the table than they return",
  granularity: "The partition grain doesn't fit the data volume",
  bucket_sizing: "Bucket count doesn't fit the data volume"
};

/** A suggested partition spec from query patterns. Cryo never changes partitioning itself. */
export const PartitionEvolutionCard = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const rec = useQuery({
    queryKey: tableKeys.partitionEvolution(tableId, token),
    queryFn: () => getPartitionEvolutionRecommendation(api, tableId),
    enabled: Boolean(tableId),
    retry: false
  });
  const data = rec.data;
  if (!data) return null;
  return (
    <div className="td-files-evolution" aria-labelledby="files-evolution-title">
      <div className="td-files-delete-head">
        <h3 id="files-evolution-title" className="text-sm font-semibold">Suggested partition change</h3>
        <Badge variant="secondary">{Math.round(data.confidence * 100)}% confidence</Badge>
      </div>
      <p className="td-files-evolution-spec">
        <code>{data.current_partition_spec || "unpartitioned"}</code> → <code>{data.recommended_partition_spec}</code>
      </p>
      <p className="td-files-caption">
        {data.trigger ? `${TRIGGER[data.trigger]}. ` : ""}
        {data.reason}
      </p>
      <dl className="td-files-evolution-facts">
        <div>
          <dt>Queries analysed</dt>
          <dd>{formatNumber(data.query_patterns_analyzed)}</dd>
        </div>
        {data.estimated_pruning_improvement_pct > 0 ? (
          <div>
            <dt>Pruning gain (estimate)</dt>
            <dd>~{Math.round(data.estimated_pruning_improvement_pct)}%</dd>
          </div>
        ) : null}
        {data.scan_ratio != null ? (
          <div>
            <dt>Read vs returned</dt>
            <dd>{data.scan_ratio.toFixed(1)}×</dd>
          </div>
        ) : null}
        <div>
          <dt>Column cardinality</dt>
          <dd>{data.cardinality_estimate}</dd>
        </div>
      </dl>
      {data.warnings.length ? (
        <ul className="td-files-evolution-warnings">
          {data.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
      <p className="td-files-caption">
        Partition evolution only changes metadata. New writes use the new spec; existing files keep the old one until
        compaction rewrites them, and queries still prune both. Cryo doesn't run this change. Apply it with your
        engine's ALTER TABLE once the table owner agrees.
      </p>
    </div>
  );
};
