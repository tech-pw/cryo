import { useQuery } from "@tanstack/react-query";

import { drainHint, getEngineLoad, type EngineLoadRow } from "../../api/engineLoad";
import { useAuth } from "../../lib/auth";
import { cn } from "../../lib/cn";
import "./decisionFunnel.css";

const fillPercent = (row: EngineLoadRow): number => {
  if (row.slots_limit <= 0) return 0;
  return Math.min(100, Math.round((row.slots_used / row.slots_limit) * 100));
};

const probeLabel = (probe: string): string | null => {
  if (!probe || probe === "not_recorded" || probe === "unknown") return null;
  return probe.replaceAll("_", " ");
};

export const EngineLoadStrip = () => {
  const { api, token } = useAuth();
  const load = useQuery({
    queryKey: ["engine-load", token],
    queryFn: () => getEngineLoad(api),
    retry: false
  });
  const engines = load.data?.engines ?? [];
  const workers = load.data?.workers_online;
  const busy = load.data?.workers_busy;
  const hint = drainHint(load.data);

  return (
    <section className="ops-signal-card" aria-labelledby="engine-load-title">
      <header className="ops-signal-card__header">
        <h2 id="engine-load-title">Engine load</h2>
        <p>Open slots versus the configured cap.</p>
      </header>
      {load.isSuccess && workers != null ? (
        <div className="ops-load-workers">
          <p className="ops-load-row__detail">
            <span className="ops-load-row__name">Workers</span>{" "}
            {busy != null ? `${busy} of ${workers} busy` : `${workers} online`}
            {load.data.admitted_waiting != null ? ` · ${load.data.admitted_waiting} admitted waiting` : ""}
          </p>
          {hint ? (
            <p className="ops-load-workers__hint" role="status">
              {hint}
            </p>
          ) : null}
        </div>
      ) : null}
      {load.isError ? (
        <p className="ops-signal-card__empty">Engine admission unavailable. Retry from Runs.</p>
      ) : engines.length === 0 ? (
        <p className="ops-signal-card__empty">No enabled engines.</p>
      ) : (
        <ul className="ops-load-list">
          {engines.map((row) => {
            const percent = fillPercent(row);
            const open = Math.max(0, row.slots_limit - row.slots_used);
            const probe = probeLabel(row.live_probe);
            const closed = row.admission !== "open";
            return (
              <li key={row.destination_id} className="ops-load-row">
                <div className="ops-load-row__meta">
                  <span className="ops-load-row__name">{row.name}</span>
                  <span className={cn("ops-load-row__count", closed && "is-closed")}>
                    {open} of {row.slots_limit} open
                  </span>
                </div>
                <div
                  className="ops-load-row__track"
                  role="meter"
                  aria-label={`${row.name} capacity`}
                  aria-valuemin={0}
                  aria-valuemax={row.slots_limit}
                  aria-valuenow={row.slots_used}
                  aria-valuetext={`${row.slots_used} of ${row.slots_limit} slots used`}
                >
                  <span
                    className={cn("ops-load-row__fill", percent >= 85 && "is-tight", closed && "is-closed")}
                    style={{ width: `${percent}%` }}
                  />
                </div>
                <p className="ops-load-row__detail">
                  {row.slots_used} used
                  {probe ? ` · probe ${probe}` : ""}
                  {closed ? ` · admission ${row.admission.replaceAll("_", " ")}` : ""}
                </p>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
};
