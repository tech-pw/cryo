import { toast } from "sonner";

import { QueryErrorAlert } from "../../components/common";
import { Alert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Descriptions } from "../../components/ui/descriptions";
import { buildSummary, useApiVersion, versionsDiffer, webBuild } from "../../lib/buildInfo";

const orUnknown = (value: string | null | undefined) => value || "unknown";

/** Settings > About: which API, schema and web build are running (#263). */
export const About = () => {
  const version = useApiVersion();
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
