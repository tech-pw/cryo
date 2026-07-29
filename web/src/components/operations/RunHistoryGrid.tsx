import { Link } from "react-router-dom";
import type { RunRead } from "../../api/types";
import { formatTime } from "../../lib/format";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Skeleton } from "../ui/skeleton";
import { StatusTag } from "../status";

const shortId = (value?: string | null) => value?.slice(0, 8) ?? "-";

const runPath = (runId: string) => `/operations/runs/${runId}`;

export const RunHistoryGrid = ({
  runs,
  title = "Recent runs",
  loading,
  emptyText = "No runs yet."
}: {
  runs: RunRead[];
  title?: string;
  loading?: boolean;
  emptyText?: string;
}) => (
  <Card className="ops-run-history">
    <CardHeader className="pb-2">
      <CardTitle>{title}</CardTitle>
    </CardHeader>
    <CardContent>
      {loading ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, idx) => (
            <Skeleton key={idx} className="h-12 w-full" />
          ))}
        </div>
      ) : runs.length === 0 ? (
        <p className="text-sm text-muted-foreground">{emptyText}</p>
      ) : (
        <ul className="ops-run-history__list">
          {runs.map((run) => (
            <li key={run.id} className="ops-run-history__item">
              <Link to={runPath(run.id)} className="ops-run-history__link">
                <div className="flex w-full flex-col gap-0.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <code className="mono-id text-sm">{shortId(run.id)}</code>
                    <StatusTag status={run.status} />
                  </div>
                  <span className="secondary-line text-xs text-muted-foreground">{formatTime(run.created_at)}</span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </CardContent>
  </Card>
);

const STATUS_CELL: Record<string, string> = {
  succeeded: "ops-mini-bar__cell--ok",
  success: "ops-mini-bar__cell--ok",
  running: "ops-mini-bar__cell--active",
  admitted: "ops-mini-bar__cell--active",
  failed: "ops-mini-bar__cell--fail",
  cancelled: "ops-mini-bar__cell--muted",
  queued: "ops-mini-bar__cell--muted",
  scheduled: "ops-mini-bar__cell--muted",
  accepted: "ops-mini-bar__cell--muted"
};

export const MiniRunHistoryBar = ({
  runs,
  max = 10
}: {
  runs: RunRead[];
  max?: number;
}) => {
  const cells = runs.slice(0, max);
  const placeholders = Math.max(0, max - cells.length);

  return (
    <div className="ops-mini-bar" role="img" aria-label={`Last ${cells.length} runs`}>
      {cells.map((run) => (
        <Link
          key={run.id}
          to={runPath(run.id)}
          className={`ops-mini-bar__cell ${STATUS_CELL[run.status] ?? "ops-mini-bar__cell--muted"}`}
          title={`${shortId(run.id)} · ${run.status} · ${formatTime(run.created_at)}`}
          onClick={(event) => event.stopPropagation()}
        />
      ))}
      {Array.from({ length: placeholders }).map((_, idx) => (
        <span key={`ph-${idx}`} className="ops-mini-bar__cell ops-mini-bar__cell--empty" aria-hidden />
      ))}
    </div>
  );
};
