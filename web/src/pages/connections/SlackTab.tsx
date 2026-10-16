import { Hash, MessageSquare, Plus, Trash2 } from "lucide-react";
import { ConfirmAction } from "../../components/confirm";
import { Alert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { Switch } from "../../components/ui/switch";
import { ConnectionList, ConnectionRow, ConnectionSection, ConnectionStatus } from "./ConnectionList";
import { useConnectionsContext } from "./ConnectionsContext";

export const SlackTab = () => {
  const {
    role,
    slackConfigs,
    setSlackDrawer,
    testSlackMutation,
    deleteSlackConfigMutation,
    sendReportToSlack,
    setSendReportToSlack,
    reportUseAi,
    setReportUseAi,
    dailyReport,
    dailyReportResult
  } = useConnectionsContext();
  const canWrite = role !== "viewer";

  const newButton = canWrite ? (
    <Button variant="primary" onClick={() => setSlackDrawer("new")}>
      <Plus aria-hidden="true" />
      New Slack connection
    </Button>
  ) : undefined;

  return (
    <ConnectionSection
      title="Slack delivery"
      description="Send daily anomaly reports to your workspace. Add a webhook, then choose its channel and reporting schedule."
      action={newButton}
    >
      <div className="flex flex-col gap-2">
        <h3 className="text-[13px] font-semibold text-[var(--color-text-secondary)]">Slack connections</h3>
        <ConnectionList
          label="Slack connections"
          loading={slackConfigs.isLoading}
          emptyTitle="No Slack connections yet"
          emptyDescription={
            canWrite
              ? "Add a Slack webhook to deliver daily anomaly reports and test the connection."
              : "An admin can add a Slack webhook to start delivering reports."
          }
        >
          {(slackConfigs.data ?? []).map((config) => (
            <ConnectionRow
              key={config.id ?? `slack-${config.name}-${config.channel}`}
              icon={<Hash />}
              name={config.name}
              kind={config.channel ?? "Webhook channel"}
              enabled={Boolean(config.enabled)}
              status={<ConnectionStatus enabled={Boolean(config.enabled)} />}
              badges={config.is_default ? <Badge variant="default">Default</Badge> : null}
              details={[
                { label: "Webhook", value: config.has_webhook ? "Configured" : "Missing" },
                {
                  label: "Daily report",
                  value: config.daily_report_enabled
                    ? `${config.daily_report_cron ?? ""}${config.daily_report_use_ai ? " · AI summary" : ""}`
                    : "Off",
                  mono: Boolean(config.daily_report_enabled)
                }
              ]}
              actions={
                <>
                  <Button size="sm" variant="secondary" onClick={() => setSlackDrawer(config)}>
                    Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    loading={testSlackMutation.isPending}
                    disabled={!config.has_webhook}
                    onClick={() => testSlackMutation.mutate(config.id)}
                  >
                    Test
                  </Button>
                  <ConfirmAction
                    title="Delete this Slack configuration?"
                    description="Daily reports and alerts will stop posting to this webhook."
                    onConfirm={() => deleteSlackConfigMutation.mutate(config.id)}
                  >
                    <Button
                      size="sm"
                      variant="ghost"
                      className="connection-delete px-2"
                      aria-label="Delete"
                      title={`Delete ${config.name}`}
                      loading={deleteSlackConfigMutation.isPending}
                    >
                      {deleteSlackConfigMutation.isPending ? null : <Trash2 aria-hidden="true" />}
                    </Button>
                  </ConfirmAction>
                </>
              }
            />
          ))}
        </ConnectionList>
      </div>
      {canWrite ? (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base tracking-[-0.01em]">Manual daily report</CardTitle>
            <p className="text-sm text-muted-foreground">
              Generate a report now, and optionally post it through the default Slack connection.
            </p>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
                <label className="inline-flex items-center gap-2.5 text-sm">
                  <Switch checked={sendReportToSlack} onCheckedChange={setSendReportToSlack} />
                  Send to Slack
                </label>
                <label className="inline-flex items-center gap-2.5 text-sm">
                  <Switch checked={reportUseAi} onCheckedChange={setReportUseAi} />
                  AI summary
                </label>
              </div>
              <Button
                className="w-full sm:w-auto"
                variant="secondary"
                loading={dailyReport.isPending}
                onClick={() => dailyReport.mutate()}
              >
                Generate report now
              </Button>
            </div>
            {dailyReportResult ? (
              <Alert
                variant="success"
                title={`Report for ${dailyReportResult.report_date}${dailyReportResult.slack_sent ? " · posted to Slack" : ""}`}
                description={
                  dailyReportResult.bottom_line ? (
                    <p className="mb-0 whitespace-pre-wrap">{dailyReportResult.bottom_line}</p>
                  ) : (
                    <pre className="inline-json">{JSON.stringify(dailyReportResult.summary, null, 2)}</pre>
                  )
                }
              />
            ) : null}
          </CardContent>
        </Card>
      ) : null}
    </ConnectionSection>
  );
};

export const SlackTabLabel = () => (
  <span className="inline-flex items-center gap-1.5">
    <MessageSquare className="size-3.5" aria-hidden="true" />
    Slack
  </span>
);
