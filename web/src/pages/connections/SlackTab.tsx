import type { ColumnDef } from "@tanstack/react-table";
import { CheckCircle2, MessageSquare, Plus, XCircle } from "lucide-react";
import { useMemo } from "react";
import type { SlackConfigRead } from "../../api/client";
import { ConfirmAction } from "../../components/confirm";
import { StatusTag } from "../../components/status";
import { Alert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { DataTable } from "../../components/ui/data-table";
import { Switch } from "../../components/ui/switch";
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

  const columns = useMemo<ColumnDef<SlackConfigRead, unknown>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Name",
        cell: ({ row }) => (
          <div className="flex items-center gap-2">
            <span className="font-medium">{row.original.name}</span>
            {row.original.is_default ? <Badge variant="default">Default</Badge> : null}
          </div>
        )
      },
      {
        accessorKey: "channel",
        header: "Channel",
        cell: ({ getValue }) => {
          const value = getValue() as string | null;
          return value ? <code className="text-xs">{value}</code> : <span className="text-muted-foreground">Webhook channel</span>;
        }
      },
      {
        accessorKey: "has_webhook",
        header: "Webhook",
        cell: ({ getValue }) =>
          getValue() ? (
            <span className="inline-flex items-center gap-1.5 text-muted-foreground">
              <CheckCircle2 className="size-3.5 text-[var(--color-status-success)]" aria-hidden="true" />
              Configured
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 text-muted-foreground">
              <XCircle className="size-3.5 text-destructive" aria-hidden="true" />
              Missing
            </span>
          )
      },
      {
        id: "daily_report",
        header: "Daily Report",
        cell: ({ row }) => (
          <div className="flex flex-col gap-0.5">
            <StatusTag status={row.original.daily_report_enabled ? "succeeded" : "skipped"} />
            {row.original.daily_report_enabled ? (
              <span className="text-[11px] text-muted-foreground">
                {row.original.daily_report_cron} {row.original.daily_report_use_ai ? "(+ AI)" : ""}
              </span>
            ) : null}
          </div>
        )
      },
      {
        accessorKey: "enabled",
        header: "Enabled",
        cell: ({ getValue }) => <StatusTag status={getValue() ? "succeeded" : "skipped"} />
      },
      {
        id: "actions",
        header: "",
        size: 220,
        cell: ({ row }) => (
          <div className="flex flex-wrap items-center gap-1">
            <Button size="sm" variant="secondary" onClick={() => setSlackDrawer(row.original)}>
              Edit
            </Button>
            <Button
              size="sm"
              variant="secondary"
              loading={testSlackMutation.isPending}
              disabled={!row.original.has_webhook}
              onClick={() => testSlackMutation.mutate(row.original.id)}
            >
              Test
            </Button>
            <ConfirmAction
              title="Delete this Slack configuration?"
              description="Daily reports and alerts will stop posting to this webhook."
              onConfirm={() => deleteSlackConfigMutation.mutate(row.original.id)}
            >
              <Button size="sm" variant="danger" loading={deleteSlackConfigMutation.isPending}>
                Delete
              </Button>
            </ConfirmAction>
          </div>
        )
      }
    ],
    [deleteSlackConfigMutation, setSlackDrawer, testSlackMutation]
  );

  return (
    <div className="flex w-full flex-col gap-3">
      <p className="tab-intro text-sm text-muted-foreground">
        Connect Slack to receive daily anomaly reports with AI-generated summaries. Configure your webhook URL,
        reporting schedule, and channel.
      </p>
      {role !== "viewer" ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle>Manual daily report</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-4">
              <label className="inline-flex items-center gap-2 text-sm">
                <Switch checked={sendReportToSlack} onCheckedChange={setSendReportToSlack} />
                Send to Slack
              </label>
              <label className="inline-flex items-center gap-2 text-sm">
                <Switch checked={reportUseAi} onCheckedChange={setReportUseAi} />
                AI summary
              </label>
            </div>
            <Button variant="primary" loading={dailyReport.isPending} onClick={() => dailyReport.mutate()}>
              Generate report now
            </Button>
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
      <div>
        <Button variant="primary" onClick={() => setSlackDrawer("new")}>
          <Plus className="size-4" aria-hidden="true" />
          New Slack connection
        </Button>
      </div>
      <DataTable
        columns={columns}
        data={slackConfigs.data ?? []}
        density="compact"
        loading={slackConfigs.isLoading}
        getRowId={(row) => row.id ?? `slack-${row.name}-${row.channel}`}
        emptyTitle="No Slack connections configured"
        emptyDescription="Add a Slack webhook to receive daily anomaly reports with AI-generated summaries."
      />
    </div>
  );
};

export const SlackTabLabel = () => (
  <span className="inline-flex items-center gap-1.5">
    <MessageSquare className="size-3.5" aria-hidden="true" />
    Slack
  </span>
);
