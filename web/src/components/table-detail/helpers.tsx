/**
 * Helper functions and small types for the table-detail page.
 *
 * Extracted from the formerly-monolithic TableDetailRedesign.tsx so the page
 * can focus on data fetching and tab assembly.
 */

import {
  AppstoreOutlined,
  ClearOutlined,
  HistoryOutlined,
  ThunderboltOutlined,
  WarningOutlined
} from "@ant-design/icons";
import type { ReactNode } from "react";

export interface SignalPointerDetail {
  title: string;
  recommendation: string;
  impactText: string;
}

export interface SignalMatrixDetail {
  what: string;
  why: string;
  current: string;
  ideal: string;
}

export const formatPartitionColumnLabel = (col?: string | null): string => {
  if (!col) return "Unpartitioned";
  const normalized = col.trim().toLowerCase();
  if (normalized === "_unpartitioned" || normalized === "unpartitioned") {
    return "Unpartitioned";
  }
  return col;
};

export const provenanceLabel = (provenance?: string | null): string => {
  switch (provenance) {
    case "assignment":
      return "manually assigned";
    case "rule":
      return "matched a rule";
    case "default":
      return "platform default";
    case "none":
      return "no preset assigned";
    default:
      return "unknown";
  }
};

export const actionIcon = (action: string, failed: boolean): ReactNode => {
  if (failed) return <WarningOutlined />;
  const normalized = action.toLowerCase();
  if (normalized.includes("orphan")) return <ClearOutlined />;
  if (normalized.includes("expire")) return <HistoryOutlined />;
  if (normalized.includes("manifest")) return <AppstoreOutlined />;
  return <ThunderboltOutlined />;
};

export const getSignalPointerDetail = (
  signalName: string,
  severity: string
): SignalPointerDetail => {
  const normName = (signalName || "").toLowerCase();
  if (normName.includes("degradation") || normName.includes("file")) {
    return {
      title: "Small-File Fragmentation Pressure",
      recommendation:
        "Execute table compaction (OPTIMIZE) to merge fragmented small files and rewrite manifests.",
      impactText: severity === "critical" ? "-20 pts" : "-8 pts"
    };
  }
  if (normName.includes("latency") || normName.includes("query")) {
    return {
      title: "Query Scanning Performance Degradation",
      recommendation:
        "Run layout optimization or tune partition filters to avoid scanning overhead.",
      impactText: severity === "critical" ? "-20 pts" : "-8 pts"
    };
  }
  if (normName.includes("cost") || normName.includes("anomaly")) {
    return {
      title: "Storage Cost & Resource Overhead Anomaly",
      recommendation:
        "Expire historical snapshots and purge orphaned storage files to recover dead bytes.",
      impactText: severity === "critical" ? "-20 pts" : "-8 pts"
    };
  }
  return {
    title: "Unhealthy Maintenance Alarm",
    recommendation: "Inspect background logs and schedule a full optimization sweep.",
    impactText: severity === "critical" ? "-20 pts" : "-8 pts"
  };
};

export const getSignalMatrix = (
  signalName: string,
  stats: any,
  statsRaw: any,
  reclaimableBytes: number,
  savingsUsd: number,
  small_pct: number
): SignalMatrixDetail => {
  const normName = (signalName || "").toLowerCase();

  if (normName.includes("degradation") || normName.includes("file")) {
    return {
      what: "Small-File Fragmentation Ratio",
      why: "A high volume of small files forces query engines (Trino/Spark) to execute sequential S3 list & GET calls during split planning, which compounds query latency and inflates network overhead.",
      current: `${small_pct.toFixed(1)}% small files (${(stats?.small_files_count ?? 0).toLocaleString()} / ${(stats?.total_files ?? 0).toLocaleString()} total files)`,
      ideal: "< 20.0% small files ratio"
    };
  }

  if (normName.includes("latency") || normName.includes("query")) {
    return {
      what: "Query Planning & Scan Latency Overheads",
      why: "Too many partitioned files or excessive manifest metadata directories require compilers to parse complex filter expressions and build large FilterNode planning trees, which causes engine planning timeouts.",
      current: `${(stats?.manifest_count ?? 0).toLocaleString()} manifest files (avg files/partition: ${(stats?.avg_files_per_partition ?? 0).toFixed(1)})`,
      ideal: "< 100 manifest files & < 50 files per partition"
    };
  }

  if (normName.includes("cost") || normName.includes("anomaly")) {
    const reclaimGb = (reclaimableBytes / (1024 * 1024 * 1024)).toFixed(1);
    return {
      what: "Storage Footprint & Orphan File Buildup",
      why: "Deleted transactional records are retained in older snapshots for point-in-time snapshot recovery. Without automated expiry and orphan removal, cold S3 storage files accumulate indefinitely.",
      current: `${reclaimGb} GB reclaimable storage (approx. $${savingsUsd.toFixed(2)}/month potential savings across ${(stats?.total_snapshots ?? 0)} snapshots)`,
      ideal: "< 50 snapshots & near-zero orphaned storage"
    };
  }

  const ratio = statsRaw?.metadata_to_data_ratio ?? 0;
  return {
    what: "Iceberg Metadata Overhead & Layout Balance",
    why: "Imbalanced metadata-to-data sizes indicate rapid committing patterns or suboptimal transaction volumes, which slows down snapshot reads and partition listing speed.",
    current: `Metadata ratio: ${(ratio * 100).toFixed(1)}% (Skew ratio: ${(stats?.partition_skew_ratio ?? 0).toFixed(1)}x)`,
    ideal: "< 5.0% metadata ratio & < 10x partition skew"
  };
};