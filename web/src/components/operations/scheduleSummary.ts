const WEEKDAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const pad2 = (value: number): string => value.toString().padStart(2, "0");

/**
 * Renders a short, best-effort English description of a five-field cron expression
 * (minute hour day-of-month month day-of-week). Falls back to the raw expression when the
 * shape isn't one of the common patterns Cryo's scanner/preset schedules use. Cron schedules
 * in Cryo always run in UTC (see `CronInput`'s help text), so the description is UTC too.
 */
export const describeCronSchedule = (cron: string | null | undefined): string | null => {
  if (!cron || !cron.trim()) return null;
  const fields = cron.trim().split(/\s+/);
  if (fields.length !== 5) return cron;
  const [minute, hour, dom, month, dow] = fields;

  if (dom === "*" && month === "*") {
    const time = /^\d+$/.test(minute) && /^\d+$/.test(hour) ? `${pad2(Number(hour))}:${pad2(Number(minute))}` : null;

    if (hour.startsWith("*/") && minute === "0" && dow === "*") {
      const step = hour.slice(2);
      return `Every ${step} hour${step === "1" ? "" : "s"} (UTC)`;
    }

    if (time && dow === "*") {
      return `Daily at ${time} UTC`;
    }

    if (time && /^\d+-\d+$/.test(dow)) {
      const [start, end] = dow.split("-").map(Number);
      if (start >= 0 && end <= 6 && start <= end) {
        return `${WEEKDAY_NAMES[start]}–${WEEKDAY_NAMES[end]} at ${time} UTC`;
      }
    }

    if (time && /^\d+$/.test(dow)) {
      const idx = Number(dow);
      if (idx >= 0 && idx <= 6) return `Weekly on ${WEEKDAY_NAMES[idx]} at ${time} UTC`;
    }
  }

  return cron;
};

const parseField = (field: string, min: number, max: number): Set<number> => {
  const values = new Set<number>();
  for (const part of field.split(",")) {
    if (part === "*") {
      for (let v = min; v <= max; v += 1) values.add(v);
      continue;
    }
    if (part.startsWith("*/")) {
      const step = Number(part.slice(2));
      if (!Number.isFinite(step) || step <= 0) continue;
      for (let v = min; v <= max; v += step) values.add(v);
      continue;
    }
    if (part.includes("-")) {
      const [start, end] = part.split("-").map(Number);
      if (Number.isFinite(start) && Number.isFinite(end)) {
        for (let v = start; v <= end; v += 1) values.add(v);
      }
      continue;
    }
    const value = Number(part);
    if (Number.isFinite(value)) values.add(value);
  }
  return values;
};

const MAX_LOOKAHEAD_MINUTES = 60 * 24 * 366;

/**
 * Computes the next UTC execution time on/after `from` for a five-field cron expression, by
 * scanning minute-by-minute (cron granularity is one minute) up to roughly one year ahead.
 * Returns null when the expression can't be parsed or no match is found in that window --
 * this is a client-side estimate for display only, not authoritative scheduling.
 */
export const nextCronRun = (cron: string | null | undefined, from: Date = new Date()): Date | null => {
  if (!cron || !cron.trim()) return null;
  const fields = cron.trim().split(/\s+/);
  if (fields.length !== 5) return null;

  const minutes = parseField(fields[0], 0, 59);
  const hours = parseField(fields[1], 0, 23);
  const doms = parseField(fields[2], 1, 31);
  const months = parseField(fields[3], 1, 12);
  const dows = parseField(fields[4], 0, 6);
  if (![minutes, hours, doms, months, dows].every((s) => s.size > 0)) return null;

  const domWildcard = fields[2] === "*";
  const dowWildcard = fields[4] === "*";

  const candidate = new Date(Date.UTC(from.getUTCFullYear(), from.getUTCMonth(), from.getUTCDate(), from.getUTCHours(), from.getUTCMinutes() + 1));

  for (let i = 0; i < MAX_LOOKAHEAD_MINUTES; i += 1) {
    const t = new Date(candidate.getTime() + i * 60_000);
    if (!months.has(t.getUTCMonth() + 1)) continue;
    const domMatch = doms.has(t.getUTCDate());
    const dowMatch = dows.has(t.getUTCDay());
    // Standard cron semantics: when both day-of-month and day-of-week are restricted, a day
    // matches if EITHER matches (union), not both. A wildcard field imposes no constraint.
    let dayMatch: boolean;
    if (domWildcard && dowWildcard) dayMatch = true;
    else if (domWildcard) dayMatch = dowMatch;
    else if (dowWildcard) dayMatch = domMatch;
    else dayMatch = domMatch || dowMatch;
    if (!dayMatch) continue;
    if (!hours.has(t.getUTCHours())) continue;
    if (!minutes.has(t.getUTCMinutes())) continue;
    return t;
  }
  return null;
};
