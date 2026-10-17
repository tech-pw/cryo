import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";

import { getGovernorOffPeakWindow } from "../../api/client";
import { EffectiveSettings } from "../../components/table-detail/maintenance/EffectiveSettings";
import { TableConfigInventory } from "../../components/table-detail/config/TableConfigInventory";
import { useAuth } from "../../lib/auth";
import { useTableDetailContext } from "./TableDetailContext";
import "./configTab.css";

const queryError = (error: unknown): string => (error instanceof Error ? error.message : "Request failed");

/**
 * Every knob that decides what maintenance does to this table, in one place.
 *
 * Split from the Maintenance tab deliberately: Maintenance answers "what ran and
 * what runs next", Config answers "what are the rules". Before this existed the
 * preset/override controls sat inside Maintenance stage 1 and the Iceberg
 * properties sat under Schema & files, so changing how a table is maintained
 * meant visiting two tabs that neither named nor linked each other.
 */
export const ConfigTab = () => {
  const { tableId } = useTableDetailContext();

  return (
    <div className="td-config">
      <section className="td-config-section" aria-labelledby="config-maintenance-title">
        <header className="td-config-head">
          <h2 id="config-maintenance-title">Maintenance settings</h2>
          <p>
            The preset decides the thresholds this table is maintained against. Overrides pin a single parameter for this
            table only and survive preset reassignment.
          </p>
        </header>
        <EffectiveSettings tableId={tableId} />
      </section>

      <section className="td-config-section" aria-labelledby="config-schedule-title">
        <header className="td-config-head">
          <h2 id="config-schedule-title">Scheduling window</h2>
          <p>
            Heavy operations are deferred outside the off-peak window so compaction does not land in the middle of a
            busy read period.
          </p>
        </header>
        <OffPeakWindow />
      </section>

      <section className="td-config-section" aria-labelledby="config-properties-title">
        <header className="td-config-head">
          <h2 id="config-properties-title">Table configuration</h2>
          <p>
            Every setting that shapes how this table is written, committed and cleaned up, read live from the catalog.
            Each row shows where its value comes from, what Cryo recommends, and how risky a change is. Stage edits,
            review them together, then apply in one audited commit.
          </p>
        </header>
        <TableConfigInventory tableId={tableId} />
      </section>
    </div>
  );
};

/**
 * Off-peak is a governor-wide setting (`offpeak_start_ist` / `offpeak_end_ist`),
 * not a per-table one, so this reads it rather than offering an edit control
 * that would imply a per-table override the backend cannot honour.
 */
const OffPeakWindow = () => {
  const { api, token } = useAuth();
  const offPeak = useQuery({
    queryKey: ["governor", "offpeak", token],
    queryFn: () => getGovernorOffPeakWindow(api),
    retry: false
  });

  if (offPeak.isLoading) return <p className="td-config-muted">Loading window…</p>;
  if (offPeak.isError) return <p role="alert">{queryError(offPeak.error)}</p>;

  const data = offPeak.data;
  if (!data) return null;

  return (
    <div className="td-config-offpeak">
      {data.enabled ? (
        <p className="td-config-offpeak-window">
          <span className="td-config-offpeak-hours">
            {String(data.start_hour_ist).padStart(2, "0")}:00 – {String(data.end_hour_ist).padStart(2, "0")}:00 IST
          </span>
          <span className="td-config-muted">Heavy operations run only inside this window.</span>
        </p>
      ) : (
        <p className="td-config-offpeak-window">
          <span className="td-config-offpeak-hours td-config-offpeak-hours--off">Any hour</span>
          <span className="td-config-muted">
            No off-peak restriction is configured, so heavy operations may be admitted at any time of day.
          </span>
        </p>
      )}
      <p className="td-config-muted">
        This window applies to every table the governor admits, not just this one.{" "}
        <Link className="td-config-link" to="/settings/admin">
          Governor settings <ArrowUpRight size={13} aria-hidden="true" />
        </Link>
      </p>
    </div>
  );
};
