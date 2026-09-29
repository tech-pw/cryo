import {
  MAINTENANCE_OPERATION_ORDER as CORRECT_ORDER,
  type MaintenanceOperation
} from "./operationOrder";

export const MAINTENANCE_BATCH_STORAGE_VERSION = 1;
export const MAINTENANCE_BATCH_CAP = 100;
export const MAINTENANCE_BATCH_POINTER_KEY = `cryo.maintenance-batch.v${MAINTENANCE_BATCH_STORAGE_VERSION}.pointer`;
export const MAINTENANCE_BATCH_CHANGE_EVENT = "cryo:maintenance-batch-change";
export const NOT_ESTIMATED = "Not estimated";

const STORAGE_KEY_PREFIX = `cryo.maintenance-batch.v${MAINTENANCE_BATCH_STORAGE_VERSION}:`;
const FORBIDDEN_MEMBERSHIP_KEYS = new Set(["pageIds", "tableIds", "table_ids"]);

export interface BatchIdentity {
  installationId: string;
  principalId: string;
  tenantId: string;
}

export interface BatchTableRef {
  id: string;
  label: string;
  catalog?: string;
  schemaName?: string;
  table?: string;
  sourceId?: string;
}

export interface BatchFilterReceipt {
  kind: "filter_receipt";
  id: string;
  status: "pending";
  filter: unknown;
  sourceId: string | null;
  view: string | null;
  label: string;
  declaredCount: number | null;
  addedAt: string;
}

export interface BatchExclusion {
  tableId: string;
  excludedAt: string;
}

export const BATCH_RECIPE_IDS = ["compact", "compact_and_clean_up", "metadata_only"] as const;
export type BatchRecipeId = (typeof BATCH_RECIPE_IDS)[number];

export interface BatchRecipe {
  id: BatchRecipeId;
  label: string;
  operations: readonly MaintenanceOperation[];
  mode: "standard" | "manifest_only";
}

const RECIPE_OPERATIONS: Record<BatchRecipeId, readonly MaintenanceOperation[]> = {
  compact: ["optimize"],
  compact_and_clean_up: ["optimize", "optimize_manifests", "expire_snapshots", "remove_orphan_files"],
  metadata_only: ["optimize_manifests"]
};

export const BATCH_RECIPES: Record<BatchRecipeId, BatchRecipe> = {
  compact: {
    id: "compact",
    label: "Compact",
    operations: orderedRecipeOperations("compact"),
    mode: "standard"
  },
  compact_and_clean_up: {
    id: "compact_and_clean_up",
    label: "Compact and clean up",
    operations: orderedRecipeOperations("compact_and_clean_up"),
    mode: "standard"
  },
  metadata_only: {
    id: "metadata_only",
    label: "Metadata only",
    operations: orderedRecipeOperations("metadata_only"),
    mode: "manifest_only"
  }
};

export interface MaintenanceBatchDraft {
  version: typeof MAINTENANCE_BATCH_STORAGE_VERSION;
  identity: BatchIdentity;
  revision: number;
  tables: BatchTableRef[];
  receipts: BatchFilterReceipt[];
  exclusions: BatchExclusion[];
  recipe: BatchRecipeId;
  updatedAt: string;
}

export interface AddMatchingInput {
  filter: unknown;
  sourceId?: string | null;
  view?: string | null;
  label?: string;
  declaredCount?: number | null;
  pageIds?: readonly string[];
}

export interface BatchStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
  key?(index: number): string | null;
  readonly length?: number;
}

export interface BatchStoreOptions {
  storage?: BatchStorage;
  cap?: number;
}

export interface BatchAddSuccess {
  ok: true;
  draft: MaintenanceBatchDraft;
  added: number;
  alreadyIn: number;
  exclusionCleared: number;
}

export interface BatchAddRejection {
  ok: false;
  draft: MaintenanceBatchDraft;
  code: "cap_exceeded" | "unserializable";
  message: string;
}

export type BatchAddResult = BatchAddSuccess | BatchAddRejection;

export function maintenanceBatchStorageKey(identity: BatchIdentity): string {
  return `${STORAGE_KEY_PREFIX}${encodeURIComponent(identity.installationId)}:${encodeURIComponent(identity.principalId)}:${encodeURIComponent(identity.tenantId)}`;
}

export function batchCapMessage(cap = MAINTENANCE_BATCH_CAP): string {
  return `This selection would exceed the ${cap}-table batch limit; narrow it`;
}

export function orderedRecipeOperations(recipe: BatchRecipeId): MaintenanceOperation[] {
  const selected = new Set(RECIPE_OPERATIONS[recipe]);
  return CORRECT_ORDER.filter((operation) => selected.has(operation));
}

export function formatBatchTableLabel(parts: {
  catalog?: string | null;
  schema?: string | null;
  table?: string | null;
}): string {
  return [parts.catalog, parts.schema, parts.table]
    .map((part) => (typeof part === "string" ? part.trim() : ""))
    .filter(Boolean)
    .join(" › ");
}

export function estimateText(value: string | number | null | undefined): string {
  if (typeof value === "number") {
    if (!Number.isFinite(value) || value === 0) return NOT_ESTIMATED;
    return String(value);
  }
  if (typeof value !== "string") return NOT_ESTIMATED;
  const trimmed = value.trim();
  if (!trimmed || /^0(?:\.0+)?(?:\s|$)/.test(trimmed)) return NOT_ESTIMATED;
  return trimmed;
}

export function runButtonLabel(count: number): string {
  const ready = sanitizeCount(count);
  return `Run maintenance on ${ready} ${ready === 1 ? "table" : "tables"}`;
}

export function sparkJobsLabel(count: number): string {
  const ready = sanitizeCount(count);
  return `${ready} separate Spark ${ready === 1 ? "job" : "jobs"}`;
}

export function batchHighlightClass(reducedMotion: boolean, active: boolean): string {
  if (reducedMotion || !active) return "";
  return "motion-highlight";
}

export function createEmptyDraft(identity: BatchIdentity, at = new Date().toISOString()): MaintenanceBatchDraft {
  return {
    version: MAINTENANCE_BATCH_STORAGE_VERSION,
    identity: { ...identity },
    revision: 0,
    tables: [],
    receipts: [],
    exclusions: [],
    recipe: "compact_and_clean_up",
    updatedAt: at
  };
}

export function membershipSummary(draft: MaintenanceBatchDraft): string {
  const known = draft.tables.length;
  const noun = known === 1 ? "table" : "tables";
  if (draft.receipts.length === 0) return `${known} ${noun}`;
  return `${known} ${noun} · Resolving…`;
}

export function addTablesToDraft(
  draft: MaintenanceBatchDraft,
  incoming: readonly BatchTableRef[],
  cap = MAINTENANCE_BATCH_CAP
): BatchAddResult {
  const uniqueIncoming = dedupeIncoming(incoming);
  const existingIds = new Set(draft.tables.map((table) => table.id));
  const alreadyIn = uniqueIncoming.filter((table) => existingIds.has(table.id)).length;
  const toAdd = uniqueIncoming.filter((table) => !existingIds.has(table.id));
  if (toAdd.length === 0) {
    return { ok: true, draft, added: 0, alreadyIn, exclusionCleared: 0 };
  }
  if (knownLoad(draft) + toAdd.length > cap) {
    return rejectCap(draft, cap);
  }
  const addedIds = new Set(toAdd.map((table) => table.id));
  const exclusionCleared = draft.exclusions.filter((entry) => addedIds.has(entry.tableId)).length;
  return {
    ok: true,
    added: toAdd.length,
    alreadyIn,
    exclusionCleared,
    draft: touch(draft, {
      tables: [...draft.tables, ...toAdd],
      exclusions: draft.exclusions.filter((entry) => !addedIds.has(entry.tableId))
    })
  };
}

export function addMatchingToDraft(
  draft: MaintenanceBatchDraft,
  input: AddMatchingInput,
  cap = MAINTENANCE_BATCH_CAP
): BatchAddResult {
  const filter = cloneFilter(input.filter);
  if (filter === undefined) {
    return {
      ok: false,
      draft,
      code: "unserializable",
      message: "This selection could not be saved."
    };
  }
  const declaredCount = finiteCount(input.declaredCount);
  const projected = knownLoad(draft) + (declaredCount ?? 0);
  if (projected > cap || (declaredCount == null && knownLoad(draft) >= cap)) {
    return rejectCap(draft, cap);
  }
  const receipt: BatchFilterReceipt = {
    kind: "filter_receipt",
    id: newId(),
    status: "pending",
    filter,
    sourceId: optionalText(input.sourceId),
    view: optionalText(input.view),
    label: input.label?.trim() || "All matching",
    declaredCount,
    addedAt: new Date().toISOString()
  };
  return {
    ok: true,
    added: 0,
    alreadyIn: 0,
    exclusionCleared: 0,
    draft: touch(draft, { receipts: [...draft.receipts, receipt] })
  };
}

export function excludeFromDraft(draft: MaintenanceBatchDraft, tableId: string): MaintenanceBatchDraft {
  const id = tableId.trim();
  if (!id) return draft;
  const tables = draft.tables.filter((table) => table.id !== id);
  const already = draft.exclusions.some((entry) => entry.tableId === id);
  if (tables.length === draft.tables.length && already) return draft;
  return touch(draft, {
    tables,
    exclusions: already
      ? draft.exclusions
      : [...draft.exclusions, { tableId: id, excludedAt: new Date().toISOString() }]
  });
}

export function withRecipe(draft: MaintenanceBatchDraft, recipe: BatchRecipeId): MaintenanceBatchDraft {
  if (draft.recipe === recipe) return draft;
  return touch(draft, { recipe });
}

export function clearMembership(draft: MaintenanceBatchDraft): MaintenanceBatchDraft {
  if (draft.tables.length === 0 && draft.receipts.length === 0 && draft.exclusions.length === 0) {
    return draft;
  }
  return touch(draft, { tables: [], receipts: [], exclusions: [] });
}

export function loadDraft(identity: BatchIdentity, options?: BatchStoreOptions): MaintenanceBatchDraft {
  const storage = resolveStorage(options?.storage);
  const key = maintenanceBatchStorageKey(identity);
  if (!storage) return createEmptyDraft(identity);
  forgetOtherIdentity(storage, key);
  const raw = readRaw(storage, key);
  if (raw == null) return createEmptyDraft(identity);
  const parsed = parseJson(raw);
  const normalized = parsed ? toDraft(parsed, identity) : null;
  if (!normalized || (parsed && containsForbiddenMembershipKeys(parsed))) {
    const next = normalized ?? createEmptyDraft(identity);
    writeRaw(storage, key, JSON.stringify(next));
    return next;
  }
  return normalized;
}

export function saveDraft(draft: MaintenanceBatchDraft, options?: BatchStoreOptions): void {
  const storage = resolveStorage(options?.storage);
  if (!storage) return;
  const key = maintenanceBatchStorageKey(draft.identity);
  forgetOtherIdentity(storage, key);
  writeRaw(storage, key, JSON.stringify(draft));
  notify(draft.identity);
}

export function addTables(
  identity: BatchIdentity,
  tables: readonly BatchTableRef[],
  options?: BatchStoreOptions
): BatchAddResult {
  const current = loadDraft(identity, options);
  const result = addTablesToDraft(current, tables, options?.cap ?? MAINTENANCE_BATCH_CAP);
  if (result.ok && result.draft !== current) saveDraft(result.draft, options);
  return result;
}

export function addMatchingFilter(
  identity: BatchIdentity,
  input: AddMatchingInput,
  options?: BatchStoreOptions
): BatchAddResult {
  const current = loadDraft(identity, options);
  const result = addMatchingToDraft(current, input, options?.cap ?? MAINTENANCE_BATCH_CAP);
  if (result.ok && result.draft !== current) saveDraft(result.draft, options);
  return result;
}

export function excludeTable(
  identity: BatchIdentity,
  tableId: string,
  options?: BatchStoreOptions
): MaintenanceBatchDraft {
  const current = loadDraft(identity, options);
  const next = excludeFromDraft(current, tableId);
  if (next !== current) saveDraft(next, options);
  return next;
}

export function setBatchRecipe(
  identity: BatchIdentity,
  recipe: BatchRecipeId,
  options?: BatchStoreOptions
): MaintenanceBatchDraft {
  const current = loadDraft(identity, options);
  const next = withRecipe(current, recipe);
  if (next !== current) saveDraft(next, options);
  return next;
}

export function clearDraft(identity: BatchIdentity, options?: BatchStoreOptions): MaintenanceBatchDraft {
  const storage = resolveStorage(options?.storage);
  const key = maintenanceBatchStorageKey(identity);
  if (storage) {
    removeRaw(storage, key);
    const pointer = readRaw(storage, MAINTENANCE_BATCH_POINTER_KEY);
    if (pointer === key) removeRaw(storage, MAINTENANCE_BATCH_POINTER_KEY);
  }
  const empty = createEmptyDraft(identity);
  notify(identity);
  return empty;
}

function rejectCap(draft: MaintenanceBatchDraft, cap: number): BatchAddRejection {
  return { ok: false, draft, code: "cap_exceeded", message: batchCapMessage(cap) };
}

function touch(
  draft: MaintenanceBatchDraft,
  patch: Partial<Pick<MaintenanceBatchDraft, "tables" | "receipts" | "exclusions" | "recipe">>
): MaintenanceBatchDraft {
  return {
    ...draft,
    ...patch,
    revision: draft.revision + 1,
    updatedAt: new Date().toISOString()
  };
}

function knownLoad(draft: MaintenanceBatchDraft): number {
  return (
    draft.tables.length +
    draft.receipts.reduce((sum, receipt) => sum + (receipt.declaredCount ?? 0), 0)
  );
}

function dedupeIncoming(incoming: readonly BatchTableRef[]): BatchTableRef[] {
  const seen = new Set<string>();
  const tables: BatchTableRef[] = [];
  for (const table of incoming) {
    const normalized = normalizeTable(table);
    if (!normalized || seen.has(normalized.id)) continue;
    seen.add(normalized.id);
    tables.push(normalized);
  }
  return tables;
}

function normalizeTable(table: BatchTableRef | null | undefined): BatchTableRef | null {
  const id = typeof table?.id === "string" ? table.id.trim() : "";
  if (!id) return null;
  const label = typeof table.label === "string" && table.label.trim() ? table.label.trim() : id;
  const next: BatchTableRef = { id, label };
  const catalog = optionalText(table.catalog);
  const schemaName = optionalText(table.schemaName);
  const tableName = optionalText(table.table);
  const sourceId = optionalText(table.sourceId);
  if (catalog) next.catalog = catalog;
  if (schemaName) next.schemaName = schemaName;
  if (tableName) next.table = tableName;
  if (sourceId) next.sourceId = sourceId;
  return next;
}

function optionalText(value: string | null | undefined): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed || null;
}

function finiteCount(value: number | null | undefined): number | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return null;
  return Math.trunc(value);
}

function sanitizeCount(count: number): number {
  if (!Number.isFinite(count) || count <= 0) return 0;
  return Math.trunc(count);
}

function cloneFilter(filter: unknown): unknown | undefined {
  try {
    return JSON.parse(JSON.stringify(filter ?? null)) as unknown;
  } catch {
    return undefined;
  }
}

function newId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `receipt-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function resolveStorage(storage?: BatchStorage): BatchStorage | null {
  if (storage) return storage;
  try {
    if (typeof sessionStorage === "undefined") return null;
    return sessionStorage;
  } catch {
    return null;
  }
}

function readRaw(storage: BatchStorage, key: string): string | null {
  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

function writeRaw(storage: BatchStorage, key: string, value: string): void {
  try {
    storage.setItem(key, value);
  } catch {
    return;
  }
}

function removeRaw(storage: BatchStorage, key: string): void {
  try {
    storage.removeItem(key);
  } catch {
    return;
  }
}

function forgetOtherIdentity(storage: BatchStorage, key: string): void {
  const pointer = readRaw(storage, MAINTENANCE_BATCH_POINTER_KEY);
  if (pointer && pointer !== key) removeRaw(storage, pointer);
  if (pointer !== key) writeRaw(storage, MAINTENANCE_BATCH_POINTER_KEY, key);
  const stale: string[] = [];
  try {
    const length = storage.length ?? 0;
    if (typeof storage.key !== "function") return;
    for (let index = 0; index < length; index += 1) {
      const storedKey = storage.key(index);
      if (storedKey && storedKey.startsWith(STORAGE_KEY_PREFIX) && storedKey !== key) stale.push(storedKey);
    }
  } catch {
    return;
  }
  stale.forEach((storedKey) => removeRaw(storage, storedKey));
}

function notify(identity: BatchIdentity): void {
  if (typeof window === "undefined") return;
  try {
    window.dispatchEvent(new CustomEvent(MAINTENANCE_BATCH_CHANGE_EVENT, { detail: identity }));
  } catch {
    return;
  }
}

function parseJson(raw: string): unknown {
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function sameIdentity(value: unknown, identity: BatchIdentity): boolean {
  if (!isRecord(value)) return false;
  return (
    value.installationId === identity.installationId &&
    value.principalId === identity.principalId &&
    value.tenantId === identity.tenantId
  );
}

function isRecipe(value: unknown): value is BatchRecipeId {
  return value === "compact" || value === "compact_and_clean_up" || value === "metadata_only";
}

function toDraft(value: unknown, identity: BatchIdentity): MaintenanceBatchDraft | null {
  if (!isRecord(value)) return null;
  if (value.version !== MAINTENANCE_BATCH_STORAGE_VERSION) return null;
  if (!sameIdentity(value.identity, identity)) return null;
  if (!Array.isArray(value.tables) || !Array.isArray(value.receipts) || !Array.isArray(value.exclusions)) {
    return null;
  }
  if (!isRecipe(value.recipe)) return null;
  if (typeof value.revision !== "number" || !Number.isFinite(value.revision)) return null;

  const tables: BatchTableRef[] = [];
  const seen = new Set<string>();
  for (const item of value.tables) {
    if (!isRecord(item)) return null;
    const table = normalizeTable(item as unknown as BatchTableRef);
    if (!table) return null;
    if (seen.has(table.id)) continue;
    seen.add(table.id);
    tables.push(table);
  }
  if (tables.length > MAINTENANCE_BATCH_CAP) return null;

  const receipts: BatchFilterReceipt[] = [];
  for (const item of value.receipts) {
    const receipt = normalizeStoredReceipt(item);
    if (!receipt) return null;
    receipts.push(receipt);
  }

  const exclusions: BatchExclusion[] = [];
  const excluded = new Set<string>();
  for (const item of value.exclusions) {
    const exclusion = normalizeStoredExclusion(item);
    if (!exclusion) return null;
    if (excluded.has(exclusion.tableId) || seen.has(exclusion.tableId)) continue;
    excluded.add(exclusion.tableId);
    exclusions.push(exclusion);
  }

  return {
    version: MAINTENANCE_BATCH_STORAGE_VERSION,
    identity: { ...identity },
    revision: value.revision,
    tables,
    receipts,
    exclusions,
    recipe: value.recipe,
    updatedAt: typeof value.updatedAt === "string" ? value.updatedAt : new Date().toISOString()
  };
}

function normalizeStoredReceipt(value: unknown): BatchFilterReceipt | null {
  if (!isRecord(value)) return null;
  if (value.kind !== "filter_receipt" || value.status !== "pending") return null;
  if (typeof value.id !== "string" || !value.id.trim()) return null;
  if (typeof value.label !== "string") return null;
  const filter = cloneFilter(value.filter);
  if (filter === undefined) return null;
  return {
    kind: "filter_receipt",
    id: value.id,
    status: "pending",
    filter,
    sourceId: optionalText(typeof value.sourceId === "string" ? value.sourceId : null),
    view: optionalText(typeof value.view === "string" ? value.view : null),
    label: value.label,
    declaredCount: finiteCount(typeof value.declaredCount === "number" ? value.declaredCount : null),
    addedAt: typeof value.addedAt === "string" ? value.addedAt : new Date().toISOString()
  };
}

function normalizeStoredExclusion(value: unknown): BatchExclusion | null {
  if (!isRecord(value) || typeof value.tableId !== "string" || !value.tableId.trim()) return null;
  return {
    tableId: value.tableId.trim(),
    excludedAt: typeof value.excludedAt === "string" ? value.excludedAt : new Date().toISOString()
  };
}

function containsForbiddenMembershipKeys(value: unknown): boolean {
  if (Array.isArray(value)) return value.some((item) => containsForbiddenMembershipKeys(item));
  if (!isRecord(value)) return false;
  for (const key of Object.keys(value)) {
    if (FORBIDDEN_MEMBERSHIP_KEYS.has(key)) return true;
    if (containsForbiddenMembershipKeys(value[key])) return true;
  }
  return false;
}
