import { formatBytesGb } from "./format";

export const auditBytesLabel = (bytes?: number | null): string =>
  bytes == null || Number.isNaN(bytes) ? "Unknown" : formatBytesGb(bytes);

export const auditFilesLabel = (input?: number | null, output?: number | null): string => {
  if (input == null || output == null) return "Unknown";
  return `${input} → ${output}`;
};
