import { ActivityTab } from "./ActivityTab";
import { UnionableTab } from "./UnionableTab";
import { AsyncBoundary } from "../AsyncBoundary";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";

export const ActivityTabPanel = () => {
  const { api, tableId, audit } = useTableDetailContext();

  return (
    <div className="td-tab-content">
      <AsyncBoundary
        isLoading={audit.isLoading}
        isError={Boolean(audit.error)}
        error={audit.error}
        onRetry={() => void audit.refetch()}
      >
        <ActivityTab entries={audit.data} />
      </AsyncBoundary>
      <div style={{ marginTop: 16 }}>
        <UnionableTab api={api} tableId={tableId} />
      </div>
    </div>
  );
};
