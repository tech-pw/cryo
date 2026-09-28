/** One query-key factory per table-detail endpoint. Include the token so role switches do not share a cache. */

export const tableKeys = {
  detail: (id: string, token?: string | null) => ["table", id, token] as const,
  audit: (id: string, token?: string | null, limit?: number, offset?: number) =>
    ["table-audit", id, token, limit ?? null, offset ?? null] as const,
  runs: (id: string, token?: string | null, limit?: number, offset?: number) =>
    ["table-runs", id, token, limit ?? null, offset ?? null] as const,
  signals: (id: string, token?: string | null) => ["table-signals", id, token] as const,
  healthTrends: (id: string, token?: string | null, days = 30) =>
    ["table-health-trends", id, token, days] as const,
  maintenanceStatus: (id: string, token?: string | null) => ["table-maintenance-status", id, token] as const,
  effectivePreset: (id: string, token?: string | null) => ["effective-preset", id, token] as const,
  effectiveParams: (id: string, token?: string | null) => ["effective-params", id, token] as const,
  compatiblePresets: (id: string, token?: string | null) => ["compatible-presets", id, token] as const,
  presets: (token?: string | null) => ["presets", token] as const,
  destinations: (token?: string | null) => ["destinations", token] as const,
  runtime: (token?: string | null) => ["runtime-flags", token] as const,
  silentLoss: (id: string, token?: string | null) => ["silent-data-loss", id, token] as const,
  fastPath: (id: string, token?: string | null) => ["fast-path-findings", id, token] as const,
  compliance: (id: string, token?: string | null) => ["table-compliance", id, token] as const,
  partitions: (id: string, token?: string | null) => ["table-partitions", id, token] as const,
  partitionAccess: (id: string, token?: string | null) => ["table-partition-access", id, token] as const,
  history: (id: string, token?: string | null) => ["table-history", id, token] as const,
  pii: (id: string, token?: string | null) => ["table-pii", id, token] as const,
  dataQuality: (id: string, token?: string | null) => ["table-dq", id, token] as const,
  columnStats: (id: string, token?: string | null) => ["table-column-stats", id, token] as const,
  bloom: (id: string, token?: string | null) => ["table-bloom-recommendations", id, token] as const,
  properties: (id: string, token?: string | null) => ["table-property-recommendations", id, token] as const,
  cost: (id: string, token?: string | null) => ["table-cost", id, token] as const,
  tiering: (id: string, token?: string | null) => ["table-s3-tiering", id, token] as const,
  sla: (id: string, token?: string | null) => ["table-sla", id, token] as const,
  queries: (id: string, token?: string | null) => ["table-query-performance", id, token] as const,
  benefitScores: (id: string, token?: string | null) => ["table-benefit-scores", id, token] as const,
  compactionCandidates: (id: string, token?: string | null) => ["table-compaction-candidates", id, token] as const,
  protectedSnapshots: (id: string, token?: string | null) => ["table-protected-snapshots", id, token] as const,
  snapshots: (id: string, token?: string | null, page?: number) =>
    ["table-snapshots", id, token, page ?? null] as const,
  sources: (token?: string | null) => ["sources", token] as const
};
