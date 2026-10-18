import { ExternalLink, Search } from "lucide-react";
import { useMemo, useState } from "react";

import type { PropertyDetailRead, TableConfigMetadataRead } from "../../../api/types";
import { Collapsible } from "../../ui/collapsible";
import { Input } from "../../ui/input";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../ui/tooltip";
import { InfoButton } from "./RiskPopover";
import { formatDurationMs, matchesProperty, PROPERTY_KIND_LABEL } from "./tableConfig";

const formatTimestamp = (ms: number | null | undefined): string => {
  if (ms == null) return "Unknown";
  return new Date(ms).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
};

export const TableMetadataCard = ({ metadata }: { metadata: TableConfigMetadataRead }) => {
  const forever = (metadata.refs ?? []).filter((ref) => ref.retained_forever);
  return (
    <dl className="tc-meta">
      <div className="tc-meta-item">
        <dt>Format version</dt>
        <dd>v{metadata.format_version ?? "?"}</dd>
      </div>
      <div className="tc-meta-item">
        <dt>Partition spec</dt>
        <dd>
          {(metadata.partition_fields ?? []).length ? (
            <span className="tc-tags">
              {(metadata.partition_fields ?? []).map((field) => (
                <span key={field.name} className="tc-tag">
                  {field.transform === "identity" ? field.source_column ?? field.name : `${field.transform}(${field.source_column ?? field.name})`}
                </span>
              ))}
            </span>
          ) : (
            "Unpartitioned"
          )}
          {(metadata.spec_count ?? 0) > 1 ? <span className="tc-editor-hint"> · {(metadata.spec_count ?? 0)} specs over time</span> : null}
        </dd>
      </div>
      <div className="tc-meta-item">
        <dt>Sort order</dt>
        <dd>
          {(metadata.sort_fields ?? []).length ? (
            <span className="tc-tags">
              {(metadata.sort_fields ?? []).map((field, index) => (
                <span key={`${field.column}-${index}`} className="tc-tag">
                  {field.transform === "identity" ? field.column : `${field.transform}(${field.column})`} {field.direction}
                </span>
              ))}
            </span>
          ) : (
            "Unsorted"
          )}
        </dd>
      </div>
      <div className="tc-meta-item">
        <dt>Identifier fields</dt>
        <dd>
          {(metadata.identifier_fields ?? []).length ? (
            <span className="tc-tags">
              {(metadata.identifier_fields ?? []).map((name) => (
                <span key={name} className="tc-tag">
                  {name}
                </span>
              ))}
            </span>
          ) : (
            "None (upserts unavailable)"
          )}
        </dd>
      </div>
      <div className="tc-meta-item">
        <dt>Branches and tags</dt>
        <dd>
          <span className="tc-tags">
            {(metadata.refs ?? []).map((ref) => (
              <Tooltip key={ref.name}>
                <TooltipTrigger asChild>
                  <span className={ref.retained_forever ? "tc-tag tc-tag--warn" : "tc-tag"} tabIndex={0}>
                    {ref.type === "tag" ? "tag" : "branch"}: {ref.name}
                  </span>
                </TooltipTrigger>
                <TooltipContent className="max-w-72">
                  {ref.retained_forever
                    ? "Kept forever: no max-ref-age is set, so its snapshot and files survive every expiry."
                    : ref.name === "main"
                      ? "The main branch never expires; it always keeps the current snapshot."
                      : ref.max_ref_age_ms != null
                        ? `Expires after ${formatDurationMs(ref.max_ref_age_ms)} without updates.`
                        : "Expires per the table's history.expire.max-ref-age-ms."}
                </TooltipContent>
              </Tooltip>
            ))}
          </span>
          {forever.length ? (
            <span className="tc-editor-hint">
              {forever.length === 1 ? "1 ref pins" : `${forever.length} refs pin`} snapshots forever
            </span>
          ) : null}
        </dd>
      </div>
      <div className="tc-meta-item">
        <dt>Snapshots · metadata log</dt>
        <dd>
          {(metadata.snapshot_count ?? 0).toLocaleString()} · {(metadata.metadata_log_count ?? 0).toLocaleString()} previous files
        </dd>
      </div>
      <div className="tc-meta-item">
        <dt>Schema</dt>
        <dd>
          {(metadata.column_count ?? 0)} columns · schema id {metadata.schema_id ?? "?"}
          {(metadata.schema_count ?? 0) > 1 ? ` · ${(metadata.schema_count ?? 0)} versions` : ""}
        </dd>
      </div>
      <div className="tc-meta-item">
        <dt>Statistics files</dt>
        <dd>
          {(metadata.statistics_files ?? 0)} table · {(metadata.partition_statistics_files ?? 0)} partition
        </dd>
      </div>
      <div className="tc-meta-item">
        <dt>Last updated</dt>
        <dd>{formatTimestamp(metadata.last_updated_ms)}</dd>
      </div>
      <div className="tc-meta-item">
        <dt>Table UUID</dt>
        <dd className="tc-mono">{metadata.table_uuid ?? "Unknown"}</dd>
      </div>
      <div className="tc-meta-item">
        <dt>Location</dt>
        <dd className="tc-mono">{metadata.location ?? "Unknown"}</dd>
      </div>
      <div className="tc-meta-item">
        <dt>Metadata file</dt>
        <dd className="tc-mono">{metadata.metadata_location ?? "Unknown"}</dd>
      </div>
    </dl>
  );
};

export interface AllPropertiesProps {
  properties: Record<string, string>;
  details: PropertyDetailRead[];
  onShowSetting: (settingKey: string) => void;
}

export const AllProperties = ({ properties, details, onShowSetting }: AllPropertiesProps) => {
  const [query, setQuery] = useState("");
  const rows = useMemo<PropertyDetailRead[]>(() => {
    const described = new Map(details.map((detail) => [detail.key, detail]));
    return Object.entries(properties).map(
      ([key, value]) =>
        described.get(key) ?? { key, value, kind: "custom", summary: "No description available." }
    );
  }, [properties, details]);
  const entries = useMemo(() => rows.filter((row) => matchesProperty(row, query)), [rows, query]);
  const total = rows.length;

  return (
    <Collapsible label={`All table properties (${total})`}>
      <div className="tc">
        <div className="tc-toolbar">
          <div className="tc-search">
            <Search aria-hidden="true" />
            <Input
              aria-label="Search properties"
              placeholder="Search keys, values or descriptions"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
        </div>
        {entries.length ? (
          <div className="tc-props-wrap">
            <table className="tc-props">
              <thead>
                <tr>
                  <th>Key</th>
                  <th>Value</th>
                  <th>What it is</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((row) => (
                  <tr key={row.key}>
                    <td>
                      <span className="tc-prop-key">
                        {row.key}
                        <InfoButton label={row.key}>
                          <PropertyInfo detail={row} onShowSetting={onShowSetting} />
                        </InfoButton>
                      </span>
                    </td>
                    <td>{row.value}</td>
                    <td>
                      <div className="tc-prop-about">
                        <span className={`tc-kind tc-kind--${row.kind}`}>{PROPERTY_KIND_LABEL[row.kind]}</span>
                        <span className="tc-clamp">{row.summary}</span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="tc-empty">{total ? "No property matches that search." : "This table has no properties set."}</p>
        )}
      </div>
    </Collapsible>
  );
};

const PropertyInfo = ({
  detail,
  onShowSetting
}: {
  detail: PropertyDetailRead;
  onShowSetting: (settingKey: string) => void;
}) => {
  const settingKey = detail.setting_key;
  return (
  <div className="tc-pop">
    <div>
      <h4 className="tc-mono">{detail.key}</h4>
      <span className={`tc-kind tc-kind--${detail.kind}`}>{PROPERTY_KIND_LABEL[detail.kind]}</span>
      <p className="tc-summary-text tc-pop-summary">{detail.summary}</p>
    </div>
    <dl className="tc-pop-facts">
      <dt>Value</dt>
      <dd className="tc-mono">{detail.value}</dd>
    </dl>
    {settingKey ? (
      <button type="button" className="tc-apply-reco" onClick={() => onShowSetting(settingKey)}>
        Show its setting, with risk and recommendation
      </button>
    ) : null}
    {detail.doc_url ? (
      <a className="tc-doc" href={detail.doc_url} target="_blank" rel="noreferrer">
        Iceberg documentation <ExternalLink aria-hidden="true" />
      </a>
    ) : null}
  </div>
  );
};
