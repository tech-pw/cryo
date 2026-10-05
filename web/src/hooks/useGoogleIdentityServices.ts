import { useCallback, useEffect, useRef, useState } from "react";

const GSI_SRC = "https://accounts.google.com/gsi/client";

interface GoogleAccountsId {
  initialize: (options: {
    client_id: string;
    callback: (response: { credential: string }) => void;
    error_callback?: (error: { type: string }) => void;
  }) => void;
  prompt: (momentListener?: (notification: { isNotDisplayed: () => boolean; isSkippedMoment: () => boolean }) => void) => void;
}

declare global {
  interface Window {
    google?: { accounts: { id: GoogleAccountsId } };
  }
}

let gsiLoadPromise: Promise<void> | null = null;

/** Loads https://accounts.google.com/gsi/client once, shared across every caller on the page
 * (the Login page and the Settings "Test" button both need it) -- previously nothing in this
 * app loaded this script at all, so every window.google?.accounts.id.initialize(...) call
 * silently no-op'd behind the optional-chain with no error. */
const loadGsiScript = (): Promise<void> => {
  if (window.google?.accounts?.id) return Promise.resolve();
  if (gsiLoadPromise) return gsiLoadPromise;
  gsiLoadPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${GSI_SRC}"]`);
    if (existing) {
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener("error", () => reject(new Error("Failed to load Google sign-in script")), {
        once: true
      });
      return;
    }
    const script = document.createElement("script");
    script.src = GSI_SRC;
    script.async = true;
    script.defer = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Failed to load Google sign-in script"));
    document.head.appendChild(script);
  });
  return gsiLoadPromise;
};

export type GoogleIdentityStatus = "idle" | "loading" | "ready" | "error";

/** Loads the GSI script on demand and exposes a one-shot `requestIdToken(clientId)` that
 * resolves with a fresh Google ID token credential, or rejects if the popup is dismissed,
 * blocked, or the script fails to load. Used by both "Sign in with Google" and the Settings
 * page's "Test configuration" button -- the latter needs a real token to verify a candidate
 * Client ID server-side before anything is saved. */
export const useGoogleIdentityServices = () => {
  const [status, setStatus] = useState<GoogleIdentityStatus>("idle");
  const [loadError, setLoadError] = useState<string | null>(null);
  const pendingRef = useRef<{ resolve: (token: string) => void; reject: (err: Error) => void } | null>(null);

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    loadGsiScript()
      .then(() => {
        if (!cancelled) {
          setStatus("ready");
          setLoadError(null);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setStatus("error");
          setLoadError(err.message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const requestIdToken = useCallback(
    (clientId: string): Promise<string> =>
      new Promise((resolve, reject) => {
        if (!clientId.trim()) {
          reject(new Error("Enter a Client ID first."));
          return;
        }
        loadGsiScript()
          .then(() => {
            const google = window.google;
            if (!google) {
              reject(new Error("Google sign-in script did not load."));
              return;
            }
            pendingRef.current = { resolve, reject };
            google.accounts.id.initialize({
              client_id: clientId,
              callback: (response) => {
                pendingRef.current?.resolve(response.credential);
                pendingRef.current = null;
              },
              error_callback: (error) => {
                pendingRef.current?.reject(
                  new Error(
                    error.type === "popup_closed"
                      ? "Sign-in popup was closed before completing."
                      : error.type === "popup_failed_to_open"
                        ? "Sign-in popup was blocked. Allow popups for this site and try again."
                        : `Google sign-in failed to start (${error.type}). Check the Client ID and ` +
                          "Authorized JavaScript origins."
                  )
                );
                pendingRef.current = null;
              }
            });
            google.accounts.id.prompt((notification) => {
              if (notification.isNotDisplayed() || notification.isSkippedMoment()) {
                pendingRef.current?.reject(
                  new Error(
                    "Google did not show a sign-in prompt. This usually means the Client ID is " +
                      "wrong or this origin isn't in its Authorized JavaScript origins."
                  )
                );
                pendingRef.current = null;
              }
            });
          })
          .catch((err: Error) => reject(err));
      }),
    []
  );

  return { status, loadError, requestIdToken };
};
