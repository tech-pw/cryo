import { Loader2 } from "lucide-react";
import { useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { useAuth } from "../lib/auth";

/** Handles OIDC authorization-code callback and stores the IdP access token. */
export const LoginCallback = () => {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { completeOidcSignIn, error } = useAuth();

  useEffect(() => {
    const code = params.get("code");
    if (!code) {
      navigate("/", { replace: true });
      return;
    }
    void completeOidcSignIn(code).then(() => navigate("/health", { replace: true }));
  }, [completeOidcSignIn, navigate, params]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-[var(--color-layout-bg)] p-6">
      <Loader2 className="size-8 animate-spin text-primary" aria-hidden="true" />
      <p className="sr-only">Completing sign-in</p>
      {error ? (
        <p className="mt-4 text-sm text-destructive" role="alert">
          {error}
        </p>
      ) : (
        <p className="mt-4 text-sm text-muted-foreground">Completing sign-in…</p>
      )}
    </div>
  );
};
