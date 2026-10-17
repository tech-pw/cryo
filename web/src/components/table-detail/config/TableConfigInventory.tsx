import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, RefreshCw, Search } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { applyTableConfig, getTableConfig, previewTableConfig } from "../../../api/client";
import type { TableConfigResponse, TableSettingRead } from "../../../api/types";
import { useDebouncedValue } from "../../../hooks/useDebouncedValue";
import { useAuth } from "../../../lib/auth";
import { formatTime } from "../../../lib/format";
import { useWriteControl } from "../../../lib/useWriteControl";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import { AsyncBoundary } from "../../AsyncBoundary";
import { Button } from "../../ui/button";
import { Input } from "../../ui/input";
import { Segmented } from "../../ui/segmented";
import { Skeleton } from "../../ui/skeleton";
import { ConfigReviewDialog } from "./ConfigReviewDialog";
import { MaintenanceOperationsCard, OPERATIONS_SECTION_ID } from "./MaintenanceOperationsCard";
import { RiskPill } from "./RiskPopover";
import { SettingRow } from "./SettingRow";
import { AllProperties, TableMetadataCard } from "./TableMetadataCard";
import {
  describeApplyError,
  matchesFilter,
  matchesSearch,
  settingAnchor,
  toChanges,
  type SettingFilter,
  type StagedChanges
} from "./tableConfig";
import "./tableConfig.css";

const SCAN_FALLBACK_REASON = "The live catalog could not be read. Edits need a fresh read so nothing is written blind.";

const InventorySkeleton = () => (
  <div className="tc" aria-busy="true" aria-label="Loading table configuration">
    <div className="tc-summary">
      {[0, 1, 2, 3].map((i) => (
        <Skeleton key={i} className="h-[4.5rem] w-full" />
      ))}
    </div>
    <Skeleton className="h-8 w-80" />
    <Skeleton className="h-64 w-full" />
  </div>
);

export const TableConfigInventory = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const queryClient = useQueryClient();
  const operatorAccess = useWriteControl("editTableConfig");
  const adminAccess = useWriteControl("editTableConfigAdmin");

  const configKey = tableKeys.config(tableId, token);
  const config = useQuery({
    queryKey: configKey,
    queryFn: () => getTableConfig(api, tableId),
    enabled: Boolean(tableId),
    staleTime: 30_000
  });

  const [filter, setFilter] = useState<SettingFilter>("all");
  const [group, setGroup] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [staged, setStaged] = useState<StagedChanges>({});
  const [reviewOpen, setReviewOpen] = useState(false);

  const refresh = useMutation({
    mutationFn: () => getTableConfig(api, tableId, true),
    onSuccess: (data) => queryClient.setQueryData(configKey, data),
    onError: (error: Error) => toast.error(error.message)
  });

  const changes = useMemo(() => toChanges(staged), [staged]);
  const changesKey = useDebouncedValue(JSON.stringify(changes), 250);
  const liveRead = config.data?.read_from === "catalog";
  const preview = useQuery({
    queryKey: tableKeys.configPreview(tableId, token, changesKey),
    queryFn: () => previewTableConfig(api, tableId, JSON.parse(changesKey) as typeof changes),
    enabled: changes.length > 0 && changesKey !== "[]" && liveRead,
    placeholderData: keepPreviousData,
    retry: false
  });
  const previewFresh = changesKey === JSON.stringify(changes);
  const previewByKey = useMemo(
    () => new Map((changes.length ? preview.data?.changes ?? [] : []).map((change) => [change.key, change])),
    [preview.data, changes.length]
  );

  const apply = useMutation({
    mutationFn: ({ confirm, reason }: { confirm: boolean; reason: string | null }) =>
      applyTableConfig(api, tableId, { changes, confirm, reason }),
    onSuccess: (result) => {
      queryClient.setQueryData(configKey, result.config);
      setStaged({});
      setEditing(null);
      setReviewOpen(false);
      toast.success(result.message);
      void queryClient.invalidateQueries({ queryKey: ["table-audit", tableId] });
      void queryClient.invalidateQueries({ queryKey: tableKeys.effectiveParams(tableId, token) });
      void queryClient.invalidateQueries({ queryKey: tableKeys.effectivePreset(tableId, token) });
      void queryClient.invalidateQueries({ queryKey: ["property-recommendations", tableId] });
      void queryClient.invalidateQueries({ queryKey: tableKeys.detail(tableId, token) });
    },
    onError: (error: Error) => toast.error(describeApplyError(error))
  });

  const data = config.data;
  const settings = useMemo(() => data?.settings ?? [], [data]);
  const settingsByKey = useMemo(() => new Map(settings.map((s) => [s.key, s])), [settings]);

  const stage = (setting: TableSettingRead, value: string | null) => {
    setStaged((current) => {
      const next = { ...current };
      const noOp = value === null ? !setting.is_set : setting.is_set && value === setting.current_value;
      if (noOp) delete next[setting.key];
      else next[setting.key] = { value, expected: setting.current_value ?? null };
      return next;
    });
    setEditing(null);
  };

  const unstage = (key: string) =>
    setStaged((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });

  const stagedCount = Object.keys(staged).length;

  return (
    <div className="tc">
      <AsyncBoundary
        isLoading={config.isLoading}
        isError={Boolean(config.error)}
        error={config.error}
        onRetry={() => void config.refetch()}
        skeleton={<InventorySkeleton />}
      >
        {data ? (
          <InventoryBody
            data={data}
            filter={filter}
            setFilter={setFilter}
            group={group}
            setGroup={setGroup}
            search={search}
            setSearch={setSearch}
            refreshing={refresh.isPending}
            onRefresh={() => refresh.mutate()}
            operations={
              data.operations ? (
                <MaintenanceOperationsCard
                  tableId={tableId}
                  operations={data.operations}
                  configKey={configKey}
                  access={operatorAccess}
                />
              ) : null
            }
          >
            {(visible) =>
              visible.map((setting) => {
                const lockedReason = !liveRead
                  ? SCAN_FALLBACK_REASON
                  : !setting.editable
                    ? (setting.read_only_reason ?? "Read-only")
                    : null;
                return (
                  <SettingRow
                    key={setting.key}
                    setting={setting}
                    columns={data.columns ?? []}
                    staged={staged[setting.key]}
                    preview={previewFresh ? previewByKey.get(setting.key) : undefined}
                    editing={editing === setting.key}
                    lockedReason={lockedReason}
                    access={setting.min_role === "admin" ? adminAccess : operatorAccess}
                    onEdit={() => setEditing(setting.key)}
                    onCancel={() => setEditing(null)}
                    onStage={(value) => stage(setting, value)}
                    onUnstage={() => unstage(setting.key)}
                  />
                );
              })
            }
          </InventoryBody>
        ) : null}
      </AsyncBoundary>

      {stagedCount ? (
        <div className="tc-bar" role="region" aria-label="Staged configuration changes">
          <span className="tc-bar-count">{stagedCount}</span>
          <span className="tc-bar-text">
            <span>
              {stagedCount === 1 ? "1 change staged" : `${stagedCount} changes staged`}
              {preview.data && previewFresh ? (
                <>
                  {" · "}
                  {preview.data.blocked ? (
                    <span className="tc-warning tc-warning--blocked tc-inline">blocked</span>
                  ) : (
                    <RiskPill risk={preview.data.overall_risk} className="tc-inline" />
                  )}
                </>
              ) : null}
            </span>
            <span>
              {preview.error
                ? describeApplyError(preview.error)
                : preview.isFetching || !previewFresh
                  ? "Checking against the live table…"
                  : preview.data?.blocked
                    ? "Fix or undo the blocked change before applying."
                    : "Nothing is written until you review and apply."}
            </span>
          </span>
          <span className="tc-bar-buttons">
            <Button variant="ghost" onClick={() => setStaged({})}>
              Discard
            </Button>
            <Button
              variant="primary"
              disabled={!operatorAccess.allowed}
              title={operatorAccess.reason ?? undefined}
              onClick={() => {
                void preview.refetch();
                setReviewOpen(true);
              }}
            >
              Review and apply
            </Button>
          </span>
        </div>
      ) : null}

      <ConfigReviewDialog
        open={reviewOpen}
        onOpenChange={setReviewOpen}
        preview={previewFresh ? preview.data : undefined}
        previewError={preview.error ? describeApplyError(preview.error) : null}
        loading={preview.isFetching || !previewFresh}
        settings={settingsByKey}
        adminAllowed={adminAccess.allowed}
        applying={apply.isPending}
        onApply={(args) => apply.mutate(args)}
      />
    </div>
  );
};

interface InventoryBodyProps {
  data: TableConfigResponse;
  filter: SettingFilter;
  setFilter: (filter: SettingFilter) => void;
  group: string;
  setGroup: (group: string) => void;
  search: string;
  setSearch: (search: string) => void;
  refreshing: boolean;
  onRefresh: () => void;
  operations: ReactNode;
  children: (visible: TableSettingRead[]) => ReactNode;
}

const scrollToId = (id: string) =>
  window.setTimeout(() => {
    const target = document.getElementById(id);
    target?.scrollIntoView({ behavior: "smooth", block: "center" });
    target?.focus({ preventScroll: true });
  }, 0);

const InventoryBody = ({
  data,
  filter,
  setFilter,
  group,
  setGroup,
  search,
  setSearch,
  refreshing,
  onRefresh,
  operations,
  children
}: InventoryBodyProps) => {
  const excludedOps = (data.operations?.operations ?? []).filter((op) => op.excluded).length;
  const showSetting = (key: string) => {
    setFilter("all");
    setGroup("all");
    setSearch("");
    scrollToId(settingAnchor(key));
  };
  const settings = data.settings;
  const differs = settings.filter((s) => s.matches_recommendation === false).length;
  const setOnTable = settings.filter((s) => s.current_source === "table").length;
  const overrides = settings.filter((s) => s.current_source === "cryo_override").length;
  const highRisk = settings.filter((s) => s.change_risk === "high").length;

  const filtered = settings.filter((s) => matchesFilter(s, filter) && matchesSearch(s, search));
  const groupCounts = new Map<string, number>();
  for (const s of filtered) groupCounts.set(s.group, (groupCounts.get(s.group) ?? 0) + 1);
  const groups = data.groups.filter((g) => (group === "all" || g.id === group) && (groupCounts.get(g.id) ?? 0) > 0);

  return (
    <>
      <div className="tc-head">
        <span className="tc-readat">
          <span className={data.read_from === "catalog" ? "tc-readat-dot" : "tc-readat-dot tc-readat-dot--stale"} />
          {data.read_from === "catalog" ? "Live from the catalog" : "From the last scan"} · {formatTime(data.read_at)}
        </span>
        <div className="tc-head-actions">
          <Button size="sm" variant="outline" loading={refreshing} onClick={onRefresh}>
            {refreshing ? null : <RefreshCw aria-hidden="true" />}
            Re-read catalog
          </Button>
        </div>
      </div>

      {data.read_from === "scan" ? (
        <div className="tc-banner" role="status">
          <AlertTriangle aria-hidden="true" />
          <span>
            <strong>Showing the last scan, not the live table.</strong> The catalog read failed
            {data.catalog_error ? ` (${data.catalog_error})` : ""}. Values may be stale and editing is paused until a
            live read succeeds.
          </span>
        </div>
      ) : null}

      <div className="tc-summary">
        <button
          type="button"
          className={differs ? "tc-tile tc-tile--warn" : "tc-tile tc-tile--ok"}
          aria-pressed={filter === "differs"}
          onClick={() => setFilter(filter === "differs" ? "all" : "differs")}
        >
          <span className="tc-tile-label">Differs from recommendation</span>
          <span className="tc-tile-value">{differs}</span>
          <span className="tc-tile-hint">{differs ? "Click to show only these" : "Everything matches"}</span>
        </button>
        <button
          type="button"
          className="tc-tile"
          aria-pressed={filter === "set"}
          onClick={() => setFilter(filter === "set" ? "all" : "set")}
        >
          <span className="tc-tile-label">Set explicitly</span>
          <span className="tc-tile-value">{setOnTable + overrides}</span>
          <span className="tc-tile-hint">
            {setOnTable} on the table · {overrides} Cryo override{overrides === 1 ? "" : "s"}
          </span>
        </button>
        <div className="tc-tile tc-tile--danger">
          <span className="tc-tile-label">High-risk settings</span>
          <span className="tc-tile-value">{highRisk}</span>
          <span className="tc-tile-hint">Changing these needs a written reason</span>
        </div>
        <button
          type="button"
          className={excludedOps ? "tc-tile tc-tile--warn-soft" : "tc-tile"}
          onClick={() => scrollToId(OPERATIONS_SECTION_ID)}
          aria-label="Maintained by: jump to maintenance operations"
        >
          <span className="tc-tile-label">Maintained by</span>
          <span className="tc-tile-value tc-tile-value--small" title={data.preset_name ?? undefined}>
            {data.preset_name ?? "No preset"}
          </span>
          <span className="tc-tile-hint">
            {[
              data.preset_engine ? `Engine: ${data.preset_engine}` : "Cryo overrides need a preset",
              data.operations?.mode ? `${data.operations.mode} mode` : null,
              excludedOps ? `${excludedOps} operation${excludedOps === 1 ? "" : "s"} excluded` : null
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </button>
      </div>

      <div className="tc-toolbar">
        <Segmented<SettingFilter>
          aria-label="Filter settings"
          size="small"
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: `All (${settings.length})` },
            { value: "differs", label: `Differs (${differs})` },
            { value: "set", label: "Set explicitly" }
          ]}
        />
        <div className="tc-search">
          <Search aria-hidden="true" />
          <Input
            aria-label="Search settings"
            placeholder="Search settings, keys or values"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
      </div>

      <div className="tc-chips" role="group" aria-label="Setting groups">
        <button type="button" className="tc-chip" aria-pressed={group === "all"} onClick={() => setGroup("all")}>
          All groups
        </button>
        {data.groups.map((g) => (
          <button
            key={g.id}
            type="button"
            className="tc-chip"
            aria-pressed={group === g.id}
            onClick={() => setGroup(group === g.id ? "all" : g.id)}
          >
            {g.label}
            <span className="tc-chip-count">{groupCounts.get(g.id) ?? 0}</span>
          </button>
        ))}
      </div>

      {groups.length ? (
        groups.map((g) => {
          const rows = filtered.filter((s) => s.group === g.id);
          const groupDiffers = rows.filter((s) => s.matches_recommendation === false).length;
          return (
            <section key={g.id} className="tc-group" aria-labelledby={`tc-group-${g.id}`}>
              <header className="tc-group-head">
                <div>
                  <h3 id={`tc-group-${g.id}`}>{g.label}</h3>
                  <p>{g.description}</p>
                </div>
                <span className="tc-group-meta">
                  {rows.length} setting{rows.length === 1 ? "" : "s"}
                  {groupDiffers ? ` · ${groupDiffers} differ` : ""}
                </span>
              </header>
              <div className="tc-cols" aria-hidden="true">
                <span>Setting</span>
                <span>Current value</span>
                <span>Cryo recommends</span>
                <span>Change risk</span>
                <span />
              </div>
              {children(rows)}
            </section>
          );
        })
      ) : (
        <p className="tc-empty">No setting matches these filters.</p>
      )}

      {operations}

      {data.metadata ? (
        <section className="tc" aria-labelledby="tc-metadata-title">
          <h3 id="tc-metadata-title" className="tc-subtitle">
            Table metadata
          </h3>
          <TableMetadataCard metadata={data.metadata} />
        </section>
      ) : null}

      <AllProperties
        properties={data.properties ?? {}}
        details={data.property_details ?? []}
        onShowSetting={showSetting}
      />
    </>
  );
};
