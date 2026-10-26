import { useEffect, useRef } from "react";

import { useGoogleIdentityServices } from "../hooks/useGoogleIdentityServices";
import { Alert } from "./ui/alert";

const BUTTON_WIDTH = 352;

/** Google's own "Sign in with Google" button for a saved Client ID. */
export const GoogleSignInButton = ({
  clientId,
  onCredential
}: {
  clientId: string;
  onCredential: (idToken: string) => void;
}) => {
  const { status, loadError, renderButton } = useGoogleIdentityServices();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const callbackRef = useRef(onCredential);
  callbackRef.current = onCredential;

  useEffect(() => {
    if (status !== "ready" || !containerRef.current) return;
    const dark = document.documentElement.classList.contains("dark");
    const width = Math.min(BUTTON_WIDTH, containerRef.current.clientWidth || BUTTON_WIDTH);
    renderButton(containerRef.current, clientId, (idToken) => callbackRef.current(idToken), {
      width,
      theme: dark ? "filled_black" : "outline"
    });
  }, [status, clientId, renderButton]);

  if (status === "error") {
    return (
      <Alert
        variant="error"
        title="Google sign-in is unavailable"
        description={loadError ?? "The Google sign-in script did not load. Check your network and reload."}
      />
    );
  }
  return (
    <div
      ref={containerRef}
      data-testid="google-sign-in"
      aria-busy={status !== "ready"}
      className="flex min-h-[44px] w-full justify-center"
    />
  );
};
