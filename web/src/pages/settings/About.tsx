import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../lib/auth";
import { toast } from "sonner";

import { QueryErrorAlert } from "../../components/common";
import { Alert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Badge } from "../../components/ui/badge";
import { Descriptions } from "../../components/ui/descriptions";
import { SimpleTable } from "../../components/ui/simple-table";
import {
  buildSummary,
  engineImages,
  getComponentBuilds,
  groupComponentBuilds,
  shortSha,
  useApiVersion,
  versionsDiffer,
  webBuild
} from "../../lib/buildInfo";

const orUnknown = (value: string | null | undefined) => value || "unknown";

/** Settings > About: which API, schema and web build are running (#263). */
export const About = () => {
  const { token } = useAuth();
  const version = useApiVersion(token);
  const components = useQuery({
    queryKey: ["component-builds"],
    queryFn: () => getComponentBuilds(token ?? ""),
    enabled: Boolean(token)
  });
  const web = webBuild();
  const api = version.data;
  const summary = buildSummary(web, api);

  return (
    <div className="flex flex-col gap-4">
      <QueryErrorAlert error={version.error} title="Could not read the API version" />
      {versionsDiffer(web, api) ? (
        <Alert
          variant="warning"
          title="The web UI and the API are on different builds"
          description={`Web ${web.version}, API ${api?.version}. Reload the page, or finish the rollout so both run the same release.`}
        />
      ) : null}
      <Descriptions
        bordered
        column={1}
        items={[
          { label: "API version", children: orUnknown(api?.version) },
          { label: "API commit", children: <code>{orUnknown(api?.git_sha)}</code> },
          { label: "API built", children: orUnknown(api?.built_at) },
          { label: "Database schema revision", children: <code>{orUnknown(api?.schema_revision)}</code> },
          { label: "Web version", children: web.version },
          { label: "Web commit", children: <code>{orUnknown(web.gitSha)}</code> }
        ]}
      />
      <ComponentBuilds data={components.data} error={components.error} loading={components.isLoading} />
      <div className="flex items-center gap-3">
        <Button
          variant="secondary"
          size="sm"
          onClick={() => {
            void navigator.clipboard.writeText(summary).then(
              () => toast.success("Version copied"),
              () => toast.error("Could not copy")
            );
          }}
        >
          Copy for a bug report
        </Button>
        <span className="text-xs text-muted-foreground">Paste it into the "Cryo version" field of a bug report.</span>
      </div>
    </div>
  );
};

const ComponentBuilds = ({
  data,
  error,
  loading
}: {
  data: Awaited<ReturnType<typeof getComponentBuilds>> | undefined;
  error: unknown;
  loading: boolean;
}) => {
  const rows = groupComponentBuilds(data?.components ?? []);
  const engines = engineImages(data?.components ?? []);
  return (
    <section aria-labelledby="about-components" className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <h2 id="about-components" className="text-sm font-semibold">
          Components
        </h2>
        {data?.skew ? <Badge variant="warning">Version skew</Badge> : null}
      </div>
      <QueryErrorAlert error={error} title="Could not read component builds" />
      {data?.skew ? (
        <Alert
          variant="warning"
          title="Components are running different builds"
          description="Finish the rollout, or check the image digests in your values file, so every component runs the same commit."
        />
      ) : null}
      <SimpleTable
        size="small"
        loading={loading}
        emptyTitle="No components have reported yet"
        emptyDescription="Components report their build to Redis every two minutes."
        rowKey={(row) => `${row.component}-${row.git_sha ?? row.version}-${row.image ?? ""}`}
        dataSource={rows}
        columns={[
          { title: "Component", dataIndex: "component" },
          { title: "Pods", key: "instances", render: (_v, row) => row.instances },
          { title: "Version", dataIndex: "version" },
          { title: "Commit", key: "commit", render: (_v, row) => <code>{shortSha(row.git_sha) ?? "unknown"}</code> },
          { title: "Image", key: "image", ellipsis: true, render: (_v, row) => <code title={row.image ?? undefined}>{row.image ?? "unknown"}</code> }
        ]}
      />
      {engines.spark.length || engines.native.length ? (
        <Descriptions
          bordered
          column={1}
          items={[
            ...engines.spark.map((ref) => ({ label: "Spark image", children: <code className="break-all">{ref}</code> })),
            ...engines.native.map((ref) => ({ label: "Native image", children: <code className="break-all">{ref}</code> }))
          ]}
        />
      ) : null}
    </section>
  );
};
