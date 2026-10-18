import { Badge } from "../../components/ui/badge";
import { SimpleTable } from "../../components/ui/simple-table";

export type AiRouteRole = "viewer" | "editor";

export interface AiRouteAccess {
  method: "GET" | "POST";
  path: string;
  capability: string;
  role: AiRouteRole;
}

export const AI_ROUTE_ACCESS: AiRouteAccess[] = [
  {
    method: "GET",
    path: "/api/v1/ai/runs/{run_id}/diagnosis-latest",
    capability: "Latest run diagnosis",
    role: "viewer"
  },
  {
    method: "POST",
    path: "/api/v1/ai/runs/{run_id}/diagnose",
    capability: "Diagnose run failure",
    role: "viewer"
  },
  {
    method: "POST",
    path: "/api/v1/ai/runs/diagnoses/{diagnosis_id}/feedback",
    capability: "Diagnosis feedback",
    role: "editor"
  },
  {
    method: "POST",
    path: "/api/v1/ai/table-filter/translate",
    capability: "Translate table filter",
    role: "viewer"
  },
  {
    method: "POST",
    path: "/api/v1/ai/plan/narrate",
    capability: "Narrate plan",
    role: "viewer"
  },
  {
    method: "GET",
    path: "/api/v1/ai/tables/{table_id}/anomaly-brief",
    capability: "Anomaly root-cause brief",
    role: "viewer"
  },
  {
    method: "GET",
    path: "/api/v1/ai/tables/{table_id}/resource-prediction",
    capability: "Resource prediction",
    role: "viewer"
  }
];

export function AiAccessTable() {
  return (
    <section
      aria-label="AI route access, read-only"
      className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-[var(--primitive-surface)] shadow-[var(--shadow-card)]"
    >
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold tracking-[-0.01em]">Who may call the model</h2>
        <Badge variant="outline">Read-only</Badge>
      </div>
      <SimpleTable
        className="px-1 pb-1"
        columns={[
          { title: "Capability", dataIndex: "capability", key: "capability" },
          { title: "Method", dataIndex: "method", key: "method" },
          {
            title: "Route",
            dataIndex: "path",
            key: "path",
            render: (value) => <span className="font-mono text-xs [font-variant-numeric:tabular-nums]">{String(value ?? "—")}</span>
          },
          { title: "Role", dataIndex: "role", key: "role" }
        ]}
        dataSource={AI_ROUTE_ACCESS}
        rowKey={(row) => `${row.method} ${row.path}`}
      />
    </section>
  );
}
