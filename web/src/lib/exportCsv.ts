const escapeCell = (value: string | number | boolean | null | undefined): string => {
  if (value === null || value === undefined) return "";
  const text = String(value);
  if (/[",\n\r]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
};

export const exportCsv = (
  filename: string,
  headers: string[],
  rows: Array<Array<string | number | boolean | null | undefined>>
): void => {
  const lines = [headers.map(escapeCell).join(","), ...rows.map((row) => row.map(escapeCell).join(","))];
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename.endsWith(".csv") ? filename : `${filename}.csv`;
  anchor.click();
  URL.revokeObjectURL(url);
};
