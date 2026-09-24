import { Shield } from "lucide-react";
import { useMemo, useState } from "react";
import { EmptyState } from "../common";
import { AsyncBoundary } from "../AsyncBoundary";
import { Alert } from "../ui/alert";
import { Badge } from "../ui/badge";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";
import { formatSlaDays, formatSlaHours, formatDuration, formatTime } from "../../lib/format";
import { ChartCard } from "./ChartCard";
import { SimpleTablePagination } from "./SimpleTablePagination";

export const QualityTabLabel = () => (
  <span>
    <Shield className="mr-1 inline size-3.5" />
    Data Quality
  </span>
);

export const QualityTab = () => {
  const {
    mergedColumns,
    dataQuality,
    table,
    tableSla,
    slaStatus,
    qualityBreakdown
  } = useTableDetailContext();

  const [page, setPage] = useState(0);
  const pageSize = 8;

  const pagedColumns = useMemo(
    () => mergedColumns.slice(page * pageSize, (page + 1) * pageSize),
    [mergedColumns, page]
  );

  return (
    <AsyncBoundary
      isLoading={dataQuality.isLoading || table.isLoading || tableSla.isLoading}
      isError={Boolean(dataQuality.error)}
      error={dataQuality.error}
      onRetry={() => void dataQuality.refetch()}
    >
      <div className="td-tab-content">
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <ChartCard
            title="Column Quality Metrics"
            subtitle={
              dataQuality.data?.profiled_at
                ? `Profiled ${formatTime(dataQuality.data.profiled_at)}`
                : "Baseline metrics"
            }
            actions={
              dataQuality.data?.anomalies_detected !== undefined ? (
                <Badge
                  variant={dataQuality.data.anomalies_detected > 0 ? "danger" : "success"}
                  className="td-quality-anomaly-tag"
                >
                  {dataQuality.data.anomalies_detected}{" "}
                  {dataQuality.data.anomalies_detected === 1 ? "Anomaly" : "Anomalies"}
                </Badge>
              ) : undefined
            }
          >
            {dataQuality.isLoading || table.isLoading ? (
              <p className="text-sm text-muted-foreground">Loading quality metrics…</p>
            ) : mergedColumns.length === 0 ? (
              <EmptyState
                title="No column metadata yet"
                description="Ensure this table has successfully run its initial metadata scan."
              />
            ) : (
              <>
                <div className="overflow-x-auto">
                  <table className="td-schema-table">
                    <thead>
                      <tr>
                        <th style={{ width: 170 }}>Column</th>
                        <th style={{ width: 150, textAlign: "right" }}>Current Null %</th>
                        <th style={{ width: 150, textAlign: "right" }}>Baseline Null %</th>
                        <th style={{ width: 220 }}>Value Range Matrix</th>
                        <th style={{ width: 100, textAlign: "center" }}>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pagedColumns.map((item) => {
                        const rate = item.nullRate != null ? item.nullRate * 100 : 0;
                        const hasBounds =
                          item.lowerBound !== undefined &&
                          item.lowerBound !== null &&
                          item.lowerBound !== "" &&
                          item.upperBound !== undefined &&
                          item.upperBound !== null &&
                          item.upperBound !== "";
                        const hasIssues = item.dqNullSpike || item.dqValueRangeChanged;

                        return (
                          <tr key={item.name}>
                            <td>
                              <span className="td-schema-col-name">{item.name}</span>
                            </td>
                            <td style={{ textAlign: "right" }}>
                              <span className="inline-flex items-center gap-1">
                                <span className="td-quality-cell-strong">{rate.toFixed(1)}%</span>
                                {item.dqNullSpike ? (
                                  <Badge variant="danger" className="td-schema-badge-spike">
                                    Spike
                                  </Badge>
                                ) : null}
                              </span>
                            </td>
                            <td style={{ textAlign: "right" }}>
                              {item.dqNullBaseline != null ? `${item.dqNullBaseline.toFixed(1)}%` : "—"}
                            </td>
                            <td>
                              {!hasBounds ? (
                                <span className="td-bounds-empty">—</span>
                              ) : (
                                <div className="td-schema-bounds">
                                  <span
                                    className="td-bounds-text"
                                    title={`${item.lowerBound} to ${item.upperBound}`}
                                  >
                                    {String(item.lowerBound)}{" "}
                                    <span className="td-bounds-sep">to</span>{" "}
                                    {String(item.upperBound)}
                                  </span>
                                  {item.dqValueRangeChanged ? (
                                    <span title={item.dqValueRangeExplanation || "Value range shifted"}>
                                      <Badge variant="warning" className="td-quality-shift-tag">
                                        Shifted
                                      </Badge>
                                    </span>
                                  ) : null}
                                </div>
                              )}
                            </td>
                            <td style={{ textAlign: "center" }}>
                              <Badge
                                variant={hasIssues ? "danger" : "success"}
                                className="td-schema-status-tag"
                              >
                                {hasIssues ? "Failed" : "Passed"}
                              </Badge>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <SimpleTablePagination
                  page={page}
                  pageSize={pageSize}
                  total={mergedColumns.length}
                  onPageChange={setPage}
                />
              </>
            )}
          </ChartCard>

          <div className="td-quality-side-col">
            <ChartCard title="Freshness & SLA">
              {tableSla.isLoading ? (
                <div className="td-quality-checks-title">Loading…</div>
              ) : slaStatus ? (
                <div className="td-quality-side">
                  <div className="td-quality-checks">
                    <div className="td-quality-check-row">
                      <span className="td-quality-check-label">Freshness (since last commit)</span>
                      <span
                        className={
                          slaStatus.freshness_breached
                            ? "td-quality-check-value-bad"
                            : "td-quality-check-value-ok"
                        }
                      >
                        {formatSlaHours(slaStatus.freshness_hours)} /{" "}
                        {formatSlaHours(slaStatus.freshness_sla_hours)}
                      </span>
                    </div>
                    <div className="td-quality-check-row">
                      <span className="td-quality-check-label">Scan staleness</span>
                      <span className="td-quality-check-value-ok">
                        {formatSlaHours(slaStatus.scan_staleness_hours)}
                      </span>
                    </div>
                    <div className="td-quality-check-row">
                      <span className="td-quality-check-label">Maintenance recency</span>
                      <span
                        className={
                          slaStatus.maintenance_breached
                            ? "td-quality-check-value-bad"
                            : "td-quality-check-value-ok"
                        }
                      >
                        {formatSlaDays(slaStatus.maintenance_days)} /{" "}
                        {formatSlaDays(slaStatus.maintenance_sla_days)}
                      </span>
                    </div>
                    {slaStatus.p95_latency_ms !== null ? (
                      <div className="td-quality-check-row">
                        <span className="td-quality-check-label">P95 query latency</span>
                        <span
                          className={
                            slaStatus.p95_latency_breached
                              ? "td-quality-check-value-bad"
                              : "td-quality-check-value-ok"
                          }
                        >
                          {formatDuration(slaStatus.p95_latency_ms)} /{" "}
                          {formatDuration(slaStatus.p95_latency_sla_ms)}
                        </span>
                      </div>
                    ) : null}
                  </div>
                  {slaStatus.freshness_breached ||
                  slaStatus.maintenance_breached ||
                  slaStatus.p95_latency_breached ? (
                    <Alert
                      variant="error"
                      className="td-quality-alert"
                      title="SLA Breached"
                      description="One or more SLA thresholds are currently exceeded for this table."
                    />
                  ) : (
                    <Alert
                      variant="success"
                      className="td-quality-alert"
                      title="Within SLA"
                      description="Freshness, maintenance, and query latency are all within configured thresholds."
                    />
                  )}
                </div>
              ) : (
                <EmptyState
                  title="No SLA data"
                  description="This table isn't monitored for SLA thresholds yet."
                />
              )}
            </ChartCard>

            <ChartCard title="Data Drift & Alerts">
              {dataQuality.data ? (
                <div className="td-quality-side">
                  {qualityBreakdown.schemaChangeCount > 0 ? (
                    <Alert
                      variant="warning"
                      className="td-quality-alert"
                      title="Schema Drift Detected"
                      description={
                        <div className="flex flex-col gap-0.5">
                          {dataQuality.data.new_columns.length ? (
                            <div>
                              <strong className="td-quality-alert-added">New columns:</strong>{" "}
                              {dataQuality.data.new_columns.map((c, idx) => (
                                <Badge variant="success" key={`new-${idx}-${c ?? ""}`}>
                                  {c}
                                </Badge>
                              ))}
                            </div>
                          ) : null}
                          {dataQuality.data.dropped_columns.length ? (
                            <div>
                              <strong className="td-quality-alert-removed">Dropped columns:</strong>{" "}
                              {dataQuality.data.dropped_columns.map((c, idx) => (
                                <Badge variant="danger" key={`dropped-${idx}-${c ?? ""}`}>
                                  {c}
                                </Badge>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      }
                    />
                  ) : (
                    <Alert
                      variant="success"
                      className="td-quality-alert"
                      title="No Schema Drift"
                      description="Schema matches the verified baseline configuration."
                    />
                  )}
                  <div className="td-quality-checks">
                    <p className="td-quality-checks-title">Quality Checks Summary</p>
                    <div className="td-quality-check-row">
                      <span className="td-quality-check-label">Null rate checks</span>
                      <span
                        className={
                          qualityBreakdown.nullSpikeCount > 0
                            ? "td-quality-check-value-bad"
                            : "td-quality-check-value-ok"
                        }
                      >
                        {qualityBreakdown.nullSpikeCount > 0
                          ? `${qualityBreakdown.nullSpikeCount} failing`
                          : "Passed"}
                      </span>
                    </div>
                    <div className="td-quality-check-row">
                      <span className="td-quality-check-label">Value range checks</span>
                      <span
                        className={
                          qualityBreakdown.rangeShiftCount > 0
                            ? "td-quality-check-value-warn"
                            : "td-quality-check-value-ok"
                        }
                      >
                        {qualityBreakdown.rangeShiftCount > 0
                          ? `${qualityBreakdown.rangeShiftCount} shifted`
                          : "Passed"}
                      </span>
                    </div>
                    <div className="td-quality-check-row">
                      <span className="td-quality-check-label">Schema changes</span>
                      <span
                        className={
                          qualityBreakdown.schemaChangeCount > 0
                            ? "td-quality-check-value-warn"
                            : "td-quality-check-value-ok"
                        }
                      >
                        {qualityBreakdown.schemaChangeCount > 0
                          ? `${qualityBreakdown.schemaChangeCount} changed`
                          : "None"}
                      </span>
                    </div>
                    <div
                      className="td-quality-check-row"
                      title="From Iceberg commit metadata; compaction (replace) commits are excluded"
                    >
                      <span className="td-quality-check-label">Data arrival</span>
                      <span
                        className={
                          qualityBreakdown.arrivalOverdue
                            ? "td-quality-check-value-bad"
                            : "td-quality-check-value-ok"
                        }
                      >
                        {qualityBreakdown.arrivalOverdue == null
                          ? "Not enough history"
                          : qualityBreakdown.arrivalOverdue
                            ? `Overdue (${formatSlaHours(qualityBreakdown.hoursSinceDataCommit ?? 0)} silent)`
                            : "On time"}
                      </span>
                    </div>
                    <div className="td-quality-check-row">
                      <span className="td-quality-check-label">Row count</span>
                      <span
                        className={
                          qualityBreakdown.rowCountAnomaly
                            ? "td-quality-check-value-bad"
                            : "td-quality-check-value-ok"
                        }
                      >
                        {qualityBreakdown.rowCountAnomaly
                          ? `Unusual ${qualityBreakdown.rowCountAnomaly}`
                          : qualityBreakdown.rowCountBaselined
                            ? "Normal"
                            : "Not enough history"}
                      </span>
                    </div>
                    <div className="td-quality-check-row">
                      <span className="td-quality-check-label">Total tracked columns</span>
                      <span className="td-quality-check-value-ok">
                        {dataQuality.data.column_metrics.length}
                      </span>
                    </div>
                  </div>
                  <div>
                    <p className="td-quality-baseline-title">Statistical Baselines</p>
                    <p className="td-quality-baseline-note">
                      Historical baselines are dynamically updated every 30 days based on query execution history
                      and background scan snapshots.
                    </p>
                  </div>
                </div>
              ) : (
                <EmptyState
                  title="No quality insights"
                  description="Fetch or scan the table to load drift and quality metrics."
                />
              )}
            </ChartCard>
          </div>
        </div>
      </div>
    </AsyncBoundary>
  );
};
