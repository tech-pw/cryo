import { Loader2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { Alert } from "../components/ui/alert";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { OidcExchangeError, useAuth } from "../lib/auth";

type Outcome = "busy" | "missing" | "rejected" | "network";

interface OutcomeCopy {
  title: string;
  description: string;
  primaryLabel: string;
  showRetry: boolean;
}

const OUTCOME_COPY: Record<Exclude<Outcome, "busy">, OutcomeCopy> = {
  missing: {
    title: "Sign-in link is incomplete",
    description:
      "This page was opened without finishing the identity provider step, so there's no authorization code to complete sign-in with.",
    primaryLabel: "Return to sign in",
    showRetry: false
  },
  rejected: {
    title: "Sign-in was rejected",
    description:
      "The identity provider didn't accept this sign-in attempt. The link may have expired or already been used.",
    primaryLabel: "Return to sign in",
    showRetry: false
  },
  network: {
    title: "Couldn't reach the identity provider",
    description: "A network problem interrupted sign-in. Check your connection and try again.",
    primaryLabel: "Return to sign in",
    showRetry: true
  }
};

/** Handles OIDC authorization-code callback and stores the IdP access token. */
export const LoginCallback = () => {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { completeOidcSignIn } = useAuth();
  const [outcome, setOutcome] = useState<Outcome>("busy");
  const [detail, setDetail] = useState<string | null>(null);
  const recoveryRef = useRef<HTMLDivElement | null>(null);
  const inFlight = useRef(false);

  const code = params.get("code");

  const runExchange = useCallback(
    (authCode: string) => {
      if (inFlight.current) return;
      inFlight.current = true;
      setOutcome("busy");
      setDetail(null);
      void completeOidcSignIn(authCode)
        .then(() => navigate("/health", { replace: true }))
        .catch((err: unknown) => {
          if (err instanceof OidcExchangeError) {
            setOutcome(err.kind);
          } else {
            setOutcome("rejected");
          }
          setDetail(err instanceof Error ? err.message : null);
        })
        .finally(() => {
          inFlight.current = false;
        });
    },
    [completeOidcSignIn, navigate]
  );

  useEffect(() => {
    if (!code) {
      setOutcome("missing");
      return;
    }
    runExchange(code);
    // Only ever run for the authorization code present when this page first mounted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code]);

  useEffect(() => {
    if (outcome !== "busy") {
      recoveryRef.current?.focus();
    }
  }, [outcome]);

  if (outcome === "busy") {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center bg-[var(--color-layout-bg)] p-6">
        <Loader2 className="size-8 animate-spin text-primary" aria-hidden="true" />
        <p className="sr-only" role="status" aria-live="polite">
          Completing sign-in
        </p>
        <p className="mt-4 text-sm text-muted-foreground" aria-hidden="true">
          Completing sign-in…
        </p>
      </div>
    );
  }

  const copy = OUTCOME_COPY[outcome];

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-layout-bg)] p-6">
      <Card className="w-full max-w-[420px]">
        <CardHeader>
          <CardTitle>Sign-in couldn't be completed</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div ref={recoveryRef} tabIndex={-1} className="focus-visible:outline-none">
            <Alert variant="error" title={copy.title} description={copy.description} />
          </div>
          {detail ? (
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer select-none">Technical details</summary>
              <pre className="mt-1 whitespace-pre-wrap break-words">{detail}</pre>
            </details>
          ) : null}
          <div className="flex flex-col gap-2">
            {copy.showRetry && code ? (
              <Button variant="primary" onClick={() => runExchange(code)}>
                Try again
              </Button>
            ) : null}
            <Button
              variant={copy.showRetry ? "secondary" : "primary"}
              onClick={() => navigate("/", { replace: true })}
            >
              {copy.primaryLabel}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};
