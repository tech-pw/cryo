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
    year: includeYear ? "numeric" : undefined
  }).format(date);
};

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
