import type { BlockingReasonRead } from "../api/maintenanceStatus";
import { formatClock, formatTime, humanizeInstants } from "./format";

const sameLocalDay = (a: Date, b: Date) => a.toDateString() === b.toDateString();

const hoursText = (hours: number) => `${Number.isInteger(hours) ? hours : hours.toFixed(1)} h`;

/** A one-line, local-time sentence for a blocking reason; falls back to the backend summary. */
export const blockingReasonText = (reason: BlockingReasonRead, now: Date = new Date()): string => {
  const values = reason.values ?? {};
  if (reason.code === "cooldown") {
    const until = typeof values.until === "string" ? values.until : reason.until;
    const hours = Number(values.cooldown_hours);
    const untilDate = until ? new Date(until) : null;
    if (untilDate && !Number.isNaN(untilDate.getTime())) {
      const when = sameLocalDay(untilDate, now) ? formatClock(untilDate) : formatTime(until);
      return Number.isFinite(hours) && hours > 0
        ? `In cooldown until ${when} (${hoursText(hours)} after the last maintenance)`
        : `In cooldown until ${when}`;
    }
  }
  return humanizeInstants(reason.summary);
};
