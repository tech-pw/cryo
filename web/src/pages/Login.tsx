import { Lock, User } from "lucide-react";
import { useState, type FormEvent } from "react";

import type { RoleToken } from "../api/types";
import { Alert } from "../components/ui/alert";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { roleOptions, useAuth } from "../lib/auth";
import { cn } from "../lib/cn";

export const Login = () => {
  const { signIn, startOidcSignIn, loading, error, authMode, devTokenEnabled } = useAuth();
  const [subject, setSubject] = useState("operator");
  const [role, setRole] = useState<RoleToken>("operator");

  if (authMode === "oidc") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-layout-bg)] p-6">
        <Card className="w-full max-w-[420px]">
          <CardHeader>
            <CardTitle>Sign in to Cryo</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <p className="text-sm text-muted-foreground">
              Production sign-in uses your organization identity provider. Dev token issuance is
              disabled.
            </p>
            {error ? <Alert variant="error" title="Sign-in failed" description={error} /> : null}
            <Button variant="primary" loading={loading} className="w-full" onClick={startOidcSignIn}>
              <Lock aria-hidden="true" />
              Sign in with SSO
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void signIn(role, subject);
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-layout-bg)] p-6">
      <Card className="w-full max-w-[420px]">
        <CardHeader>
          <CardTitle>Sign in to Cryo</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <p className="text-sm text-muted-foreground">
              Cryo uses signed JWTs from the API. In dev, choose a role and sign in; production
              deployments disable the token endpoint and integrate your identity provider.
            </p>
            {!devTokenEnabled ? (
              <Alert
                variant="warning"
                title="Dev token endpoint disabled"
                description="Configure OIDC (CRYO_OIDC_ISSUER_URL, CRYO_OIDC_CLIENT_ID) for production sign-in."
              />
            ) : null}
            {error ? <Alert variant="error" title="Sign-in failed" description={error} /> : null}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="login-subject">Username</Label>
              <div className="relative">
                <User
                  className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                  aria-hidden="true"
                />
                <Input
                  id="login-subject"
                  className="pl-9"
                  value={subject}
                  onChange={(event) => setSubject(event.target.value)}
                  placeholder="your.name"
                  autoComplete="username"
                  required
                />
              </div>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="login-role">Role</Label>
              <select
                id="login-role"
                value={role}
                onChange={(event) => setRole(event.target.value as RoleToken)}
                className={cn(
                  "flex h-[var(--control-height)] w-full rounded-[var(--radius-default)] border border-border bg-background px-3 py-2 text-sm text-foreground",
                  "focus-visible:outline-none focus-visible:ring-[var(--focus-ring-width)] focus-visible:ring-ring focus-visible:ring-offset-[var(--focus-ring-offset)]"
                )}
                required
              >
                {roleOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <Button
              type="submit"
              variant="primary"
              loading={loading}
              disabled={!devTokenEnabled}
              className="w-full"
            >
              <Lock aria-hidden="true" />
              Sign in
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
};
