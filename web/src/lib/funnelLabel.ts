export const funnelCountLabel = (count: number | null | undefined): string =>
  count == null ? "Not recorded" : String(count);
