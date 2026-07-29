import { useQuery } from "@tanstack/react-query";
import { getTableLineage } from "../../api/client";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";
import { LineageGraph } from "./LineageGraph";

export const LineageTabPanel = () => {
  const { api, token, tableId } = useTableDetailContext();

  const lineage = useQuery({
    queryKey: ["table-lineage", token, tableId],
    queryFn: () => getTableLineage(api, tableId),
    enabled: tableId.length > 8
  });

  return (
    <div className="td-tab-content">
      <LineageGraph
        data={lineage.data}
        isLoading={lineage.isLoading}
        error={lineage.error as Error | null}
      />
    </div>
  );
};
