import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";

import { getPropertyRecommendations, type PropertyRecommendationRead } from "../../../api/client";
import type { TableSettingRead } from "../../../api/types";
import { useAuth } from "../../../lib/auth";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import type { StagedChanges } from "./tableConfig";

const BLOOM_SETTING_KEY = "write.parquet.bloom-filter-columns";

const TIER: Record<PropertyRecommendationRead["tier"], { label: string; variant: "success" | "default" | "warning" }> = {
  tier1: { label: "Metadata only", variant: "success" },
  tier2: { label: "Needs a rewrite", variant: "default" },
  tier3: { label: "Caution", variant: "warning" }
};

interface Staging {
  setting: TableSettingRead;
  value: string;
}

/** Where a suggestion lands in the inventory, so it goes through the same preview and audited apply. */
export const stagingFor = (
  item: PropertyRecommendationRead,
  settings: Map<string, TableSettingRead>
): Staging | null => {
  if (item.action === "link_bloom" && item.bloom_filter_column) {
    const setting = settings.get(BLOOM_SETTING_KEY);
    if (!setting) return null;
    const columns = new Set((setting.current_value ?? "").split(",").filter(Boolean));
    if (columns.has(item.bloom_filter_column)) return null;
    columns.add(item.bloom_filter_column);
    return { setting, value: [...columns].join(",") };
  }
  if (item.action !== "apply" || item.recommended_value == null) return null;
  const setting = settings.get(item.property_key);
  return setting ? { setting, value: item.recommended_value } : null;
};

/**
 * Suggestions the scan's property checks make that the inventory rows don't already. A row that
 * carries its own recommendation wins, so the page never shows two answers for one property.
 */
export const visibleSuggestions = (
  items: PropertyRecommendationRead[],
  settings: Map<string, TableSettingRead>
): PropertyRecommendationRead[] =>
  items.filter((item) => {
    if (item.action === "banner") return false;
    const key = item.action === "link_bloom" ? BLOOM_SETTING_KEY : item.property_key;
    const row = settings.get(key);
    if (item.action === "link_bloom") return stagingFor(item, settings) != null;
    return row?.recommended_value == null;
  });

export const ScanSuggestions = ({
  tableId,
  settings,
  staged,
  blockedReasonFor,
  onStage
}: {
  tableId: string;
  settings: Map<string, TableSettingRead>;
  staged: StagedChanges;
  blockedReasonFor: (setting: TableSettingRead) => string | null;
  onStage: (setting: TableSettingRead, value: string) => void;
}) => {
  const { api, token } = useAuth();
  const recommendations = useQuery({
    queryKey: ["property-recommendations", tableId, token],
    queryFn: () => getPropertyRecommendations(api, tableId),
    enabled: Boolean(tableId),
    retry: false
  });
  if (!recommendations.data) return null;
  const items = visibleSuggestions(recommendations.data.items, settings);
  const gcBlocked = recommendations.data.gc_blocked;
  if (!items.length && !gcBlocked) return null;

  return (
    <section className="tc-suggestions" aria-labelledby="config-suggestions-title">
      <header>
        <h3 id="config-suggestions-title">More suggestions from the last scan</h3>
        <p>Staging one adds it to the review below. Nothing is written until you apply.</p>
      </header>
      {gcBlocked ? (
        <p role="alert" className="tc-suggestion-alert">
          gc.enabled is false, so snapshot expiry and orphan cleanup are skipped on this table. Other changes still
          apply.
        </p>
      ) : null}
      <ul>
        {items.map((item) => {
          const staging = stagingFor(item, settings);
          const already = staging ? staged[staging.setting.key]?.value === staging.value : false;
          const locked = staging ? blockedReasonFor(staging.setting) : null;
          return (
            <li key={`${item.property_key}-${item.bloom_filter_column ?? ""}`}>
              <div className="tc-suggestion-text">
                <p>
                  <code>{item.action === "link_bloom" ? `bloom filter on ${item.bloom_filter_column}` : item.property_key}</code>
                  <Badge variant={TIER[item.tier].variant}>{TIER[item.tier].label}</Badge>
                </p>
                {item.action === "apply" ? (
                  <p className="tc-suggestion-values">
                    {item.current_value ?? "not set"} → <b>{item.recommended_value}</b>
                  </p>
                ) : null}
                <p className="tc-suggestion-reason">{item.reason}</p>
              </div>
              <div className="tc-suggestion-action">
                {staging ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={already || Boolean(locked)}
                    title={locked ?? undefined}
                    onClick={() => onStage(staging.setting, staging.value)}
                  >
                    {already ? "Staged" : "Stage"}
                  </Button>
                ) : item.action === "link_layout" ? (
                  <Link className="tc-suggestion-link" to={`/tables/${tableId}?tab=files#files-layout`}>
                    See layout <ArrowUpRight size={13} aria-hidden="true" />
                  </Link>
                ) : item.action === "link_run" ? (
                  <Link className="tc-suggestion-link" to={`/tables/${tableId}?tab=maintenance`}>
                    Maintenance <ArrowUpRight size={13} aria-hidden="true" />
                  </Link>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
};
