import { Alert } from "../../ui/alert";
import { formatTime } from "../../../lib/format";

export interface RemovedFromCatalogBannerProps {
  removedAt?: string | null;
  reason?: string | null;
}

/** Shown on a table the scanner found gone from its catalog (#120). The row is kept, not deleted. */
export const RemovedFromCatalogBanner = ({ removedAt, reason }: RemovedFromCatalogBannerProps) => {
  if (!removedAt) return null;
  return (
    <Alert
      variant="warning"
      title={`Removed from catalog ${formatTime(removedAt)}`}
      description={
        <>
          The last scan did not find this table in its catalog, so Cryo stopped maintaining it and
          hid it from the registry. History is kept. If the table comes back, the next scan restores
          it.
          {reason ? <span className="block">{reason}</span> : null}
        </>
      }
    />
  );
};
