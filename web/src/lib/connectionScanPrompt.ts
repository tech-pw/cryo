export type ConnectionKind = "lakehouse" | "engine";

export interface ConnectionScanNotice {
  kind: ConnectionKind;
  id: string;
  name: string;
}

const storagePrefix = (kind: ConnectionKind) =>
  kind === "lakehouse" ? "cryo.pendingScan.lakehouse." : "cryo.pendingScan.engine.";

export const markConnectionNeedsScan = (notice: ConnectionScanNotice) => {
  sessionStorage.setItem(`${storagePrefix(notice.kind)}${notice.id}`, notice.name);
};

export const isConnectionPendingScan = (kind: ConnectionKind, id: string): boolean =>
  sessionStorage.getItem(`${storagePrefix(kind)}${id}`) !== null;

export const clearConnectionPendingScan = (kind: ConnectionKind, id: string) => {
  sessionStorage.removeItem(`${storagePrefix(kind)}${id}`);
};

export const clearAllLakehousePendingScans = () => {
  const keys: string[] = [];
  for (let i = 0; i < sessionStorage.length; i++) {
    const key = sessionStorage.key(i);
    if (key?.startsWith("cryo.pendingScan.lakehouse.")) {
      keys.push(key);
    }
  }
  keys.forEach((key) => sessionStorage.removeItem(key));
};

export const readPendingLakehouseScan = (): ConnectionScanNotice | null => {
  for (let i = 0; i < sessionStorage.length; i++) {
    const key = sessionStorage.key(i);
    if (!key?.startsWith("cryo.pendingScan.lakehouse.")) continue;
    const name = sessionStorage.getItem(key);
    const id = key.slice("cryo.pendingScan.lakehouse.".length);
    if (name && id) return { kind: "lakehouse", id, name };
  }
  return null;
};

export const readConnectionScanNotice = (state: unknown): ConnectionScanNotice | null => {
  if (!state || typeof state !== "object") return null;
  const notice = (state as { connectionNotice?: ConnectionScanNotice }).connectionNotice;
  if (!notice?.id || !notice.name || !notice.kind) return null;
  return notice;
};
