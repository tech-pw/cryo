import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

import { Alert } from "./ui/alert";

type ToastKind = "info" | "success" | "warning" | "error";
interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
  description?: string;
}
interface NotificationContextValue {
  notify: (kind: ToastKind, message: string, description?: string) => void;
}

const NotificationContext = createContext<NotificationContextValue | null>(null);

export const useNotifications = (): NotificationContextValue => {
  const ctx = useContext(NotificationContext);
  if (!ctx) {
    return { notify: () => undefined };
  }
  return ctx;
};

const toastVariant = (kind: ToastKind): "info" | "success" | "warning" | "error" => kind;

export const NotificationProvider = ({ children }: { children: ReactNode }) => {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);

  const notify = useCallback((kind: ToastKind, message: string, description?: string) => {
    const id = ++seq.current;
    setToasts((prev) => [...prev, { id, kind, message, description }]);
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 5000);
  }, []);

  const value = useMemo(() => ({ notify }), [notify]);

  return (
    <NotificationContext.Provider value={value}>
      {children}
      <div
        aria-live="polite"
        aria-atomic="true"
        role="status"
        className="pointer-events-none fixed right-6 top-[72px] z-[2000] flex max-w-[380px] flex-col gap-2"
      >
        {toasts.map((t) => (
          <div key={t.id} className="pointer-events-auto">
            <Alert
              variant={toastVariant(t.kind)}
              title={t.message}
              description={t.description}
              onDismiss={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))}
            />
          </div>
        ))}
      </div>
    </NotificationContext.Provider>
  );
};
