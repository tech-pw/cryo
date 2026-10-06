import { ActivityTab } from "./ActivityTab";
import { RunsTab } from "./RunsTab";
import { UnionableTab } from "./UnionableTab";
import { AsyncBoundary } from "../AsyncBoundary";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";

export const ActivityTabPanel = () => {
  const { api, tableId, audit, runs } = useTableDetailContext();

  return (
    <div className="td-tab-content flex flex-col gap-4">
      <RunsTab
        runs={runs.data ?? []}
        isLoading={runs.isLoading}
        isError={Boolean(runs.error)}
        error={runs.error}
      />
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
