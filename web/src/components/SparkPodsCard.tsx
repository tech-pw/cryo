import { useQuery } from "@tanstack/react-query";

import { getSparkPods, podStateLabel } from "../api/sparkPods";
import { useAuth } from "../lib/auth";
import { Badge } from "./ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { SimpleTable } from "./ui/simple-table";

/** Wrapper, driver and executor pod state plus recent Warning events for a running Spark step (#488). */
export const SparkPodsCard = ({ jobId }: { jobId: string }) => {
  const { api } = useAuth();
  const pods = useQuery({
    queryKey: ["spark-pods", api.token, jobId],
    queryFn: () => getSparkPods(api, jobId),
    refetchInterval: 30_000,
    retry: false
  });
  const data = pods.data;
  if (!data || (!data.available && !data.engine_ref)) return null;

  return (
    <Card className="mb-4">
      <CardHeader className="flex flex-row flex-wrap items-center gap-2 pb-2">
        <CardTitle>Spark pods</CardTitle>
        {data.summary ? <Badge variant="danger">{data.summary}</Badge> : null}
      </CardHeader>
      <CardContent className="space-y-3">
        {!data.available ? (
          <p className="text-sm text-muted-foreground">{data.reason}</p>
        ) : (
          <>
            <SimpleTable
              size="small"
              emptyTitle="No pods yet"
              emptyDescription="spark-submit has not created the wrapper pod."
              rowKey={(row) => row.name}
              dataSource={data.pods}
              columns={[
                { title: "Role", dataIndex: "role" },
                { title: "Pod", key: "name", render: (_v, row) => <code className="break-all">{row.name}</code> },
                {
                  title: "State",
                  key: "state",
                  render: (_v, row) => (
                    <span title={row.waiting_message ?? undefined}>
                      {row.stuck ? <Badge variant="danger">{podStateLabel(row)}</Badge> : podStateLabel(row)}
                    </span>
                  )
                },
                { title: "Node", key: "node", render: (_v, row) => row.node ?? "—" }
              ]}
            />
            {data.events.length ? (
              <div>
                <h3 className="mb-1 text-sm font-semibold">Recent warnings</h3>
                <ul className="space-y-1 text-sm">
                  {data.events.map((event, index) => (
                    <li key={`${event.pod}-${event.reason}-${index}`}>
                      <span className="font-medium">{event.reason}</span>
                      {event.count > 1 ? ` ×${event.count}` : ""} <code className="text-xs">{event.pod}</code>
                      <span className="block text-muted-foreground">{event.message}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
};
