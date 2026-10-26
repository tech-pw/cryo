const compactNumber = new Intl.NumberFormat(undefined, {
  notation: "compact",
  maximumFractionDigits: 1
});

const plainNumber = new Intl.NumberFormat(undefined, {
  maximumFractionDigits: 0
});

const decimalNumber = new Intl.NumberFormat(undefined, {
  maximumFractionDigits: 1
});

const currency = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0
});

export const formatNumber = (value?: number | null, compact = false): string => {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return compact ? compactNumber.format(value) : plainNumber.format(value);
};

export const formatDecimal = (value?: number | null): string => {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return decimalNumber.format(value);
};

export const formatGb = (value?: number | null): string => {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  if (value > 0 && value < 0.1) return `${decimalNumber.format(value * 1024)} MB`;
  if (value >= 1024) return `${decimalNumber.format(value / 1024)} TB`;
  return `${decimalNumber.format(value)} GB`;
};

export const formatCurrency = (value?: number | null): string => {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return currency.format(value);
};

/** Savings estimates: "-" when there is none, "<$1" so sub-dollar rows don't read as zero. */
export const formatSavings = (value?: number | null): string => {
  if (value === null || value === undefined || Number.isNaN(value) || value <= 0) return "-";
  if (value < 1) return "<$1";
  return currency.format(value);
};

export const formatDuration = (ms?: number | null): string => {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "-";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes < 60) return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
};

const ISO_INSTANT = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/;

/**
 * The zone the install reports and schedules in (CRYO_DISPLAY_TIMEZONE, #183).
 *
 * The backend sends it on `/api/v1/runtime`. Until that arrives (and in tests), fall back
 * to the browser's zone -- the old behaviour -- so the UI is never blank or wrong-shaped
 * before the first runtime response.
 */
let displayTimeZone: string | undefined;

export const setDisplayTimeZone = (zone: string | undefined | null): void => {
  displayTimeZone = zone?.trim() || undefined;
};

export const getDisplayTimeZone = (): string | undefined => displayTimeZone;

/**
 * A short label for the display zone: "IST", "UTC", or the zone's own name.
 *
 * The UI shows the zone next to a clock so a reader can tell which one they are looking at.
 */
export const zoneLabel = (zone: string | undefined = displayTimeZone): string => {
  if (!zone) return "local";
  if (zone === "UTC") return "UTC";
  const short = zone.split("/").pop() ?? zone;
  return short.replace(/_/g, " ");
};

/** The zone to render an instant in: the install's zone, else the browser's. */
const timeZone = (): string | undefined => {
  if (displayTimeZone) return displayTimeZone;
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return undefined;
  }
};

export const formatTime = (value?: string | null): string => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  const includeYear = date.getFullYear() !== new Date().getFullYear();
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    year: includeYear ? "numeric" : undefined,
    timeZone: timeZone()
  }).format(date);
};

/** A date in the install's zone, with the year -- for "last seen" and similar. */
export const formatDate = (value?: string | null): string => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    timeZone: timeZone()
  }).format(date);
};

/**
 * A short date in the install's zone -- "12 Oct" -- for chart axes and dense tables.
 *
 * These read the browser's zone by default, which is what made the UI show two clocks on
 * one page next to the off-peak window and the run timestamps (#183).
 */
export const formatShortDate = (value: string | number | Date): string => {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    timeZone: timeZone()
  }).format(date);
};

/** A date and clock in the install's zone -- for tooltips and detail rows. */
export const formatDateTime = (value: string | number | Date): string => {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: timeZone()
  }).format(date);
};

/** The clock only, in the install's zone. */
export const formatClock = (value: string | number | Date): string => {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: timeZone()
  }).format(date);
};

/** Rewrite embedded ISO-8601 instants (the form the API stores) into the readable clock. */
export const humanizeInstants = (text: string): string =>
  text.replace(/\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?/g, (match) => {
    if (!ISO_INSTANT.test(match)) return match;
    const formatted = formatTime(match);
    return formatted === "-" ? match : formatted;
  });

export const bytesToGb = (bytes?: number | null): number =>
  bytes && Number.isFinite(bytes) ? bytes / 1024 ** 3 : 0;

export const formatBytesGb = (bytes?: number | null): string => {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return "-";
  return formatGb(bytes / 1024 ** 3);
};

export const formatPercent = (value?: number | null, digits = 0): string => {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return `${value.toFixed(digits)}%`;
};

export const formatSlaHours = (hours?: number | null): string => {
  if (hours === null || hours === undefined || Number.isNaN(hours)) return "—";
  if (hours < 1) return `${(hours * 60).toFixed(0)}m`;
  if (hours < 24) return `${hours.toFixed(1)}h`;
  return `${(hours / 24).toFixed(1)}d`;
};

export const formatSlaDays = (days?: number | null): string => {
  if (days === null || days === undefined || Number.isNaN(days)) return "—";
  if (days < 1) return `${(days * 24).toFixed(1)}h`;
  return `${days.toFixed(1)}d`;
};
