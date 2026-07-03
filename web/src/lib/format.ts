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
  if (value >= 1024) return `${decimalNumber.format(value / 1024)} TB`;
  return `${decimalNumber.format(value)} GB`;
};

export const formatCurrency = (value?: number | null): string => {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
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
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(date);
};
