import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { Alert } from "antd";

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
        style={{
          position: "fixed",
          top: 72,
          right: 24,
          zIndex: 2000,
          display: "flex",
          flexDirection: "column",
          gap: 8,
          maxWidth: 380
        }}
      >
        {toasts.map((t) => (
          <Alert
            key={t.id}
            type={t.kind}
            message={t.message}
            description={t.description}
            showIcon
            closable
            onClose={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))}
          />
        ))}
      </div>
    </NotificationContext.Provider>
  );
};