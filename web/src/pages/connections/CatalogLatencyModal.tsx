import { Alert } from "../../components/ui/alert";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle
} from "../../components/ui/dialog";
import { EmptyState } from "../../components/common";
import { useConnectionsContext } from "./ConnectionsContext";

export const CatalogLatencyModal = () => {
  const { latencySource, setLatencySource, catalogLatency } = useConnectionsContext();

  return (
    <Dialog open={Boolean(latencySource)} onOpenChange={(open) => !open && setLatencySource(null)}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {latencySource ? `Catalog latency · ${latencySource.name}` : "Catalog latency"}
          </DialogTitle>
          <DialogDescription>
            Rolling latency summaries for catalog operations on this lakehouse.
          </DialogDescription>
        </DialogHeader>
        {catalogLatency.isLoading ? (
          <p className="text-sm text-muted-foreground">Loading latency samples…</p>
        ) : catalogLatency.error ? (
          <Alert variant="error" title={(catalogLatency.error as Error).message} />
        ) : catalogLatency.data ? (
          <div className="space-y-3">
            {Object.keys(catalogLatency.data.operations).length ? (
              <div className="overflow-x-auto rounded border border-border">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/40 text-left">
                      <th className="px-3 py-2 font-medium">Operation</th>
                      <th className="px-3 py-2 font-medium">Summary</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(catalogLatency.data.operations).map(([operation, stats]) => (
                      <tr key={operation} className="border-b border-border last:border-0">
                        <td className="px-3 py-2 align-top font-mono text-xs">{operation}</td>
                        <td className="px-3 py-2">
                          <pre className="inline-json text-xs">{JSON.stringify(stats, null, 2)}</pre>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState
                title="No samples yet"
                description="Latency is recorded after catalog reads during scans."
              />
            )}
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
};
