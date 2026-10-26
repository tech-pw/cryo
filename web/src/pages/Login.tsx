import { Eye, EyeOff, Lock, User } from "lucide-react";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";

import type { RoleToken } from "../api/types";
import { CryoMark } from "../components/brand/CryoMark";
import { Alert } from "../components/ui/alert";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Segmented } from "../components/ui/segmented";
import { GoogleSignInButton } from "../components/GoogleSignInButton";
import { roleOptions, useAuth } from "../lib/auth";
import { cn } from "../lib/cn";

const SIGN_IN_RECOVERY_HINT =
  "Check your sign-in details, then try again. Contact your administrator if this continues.";

const PANEL =
  "login-panel relative w-full max-w-[400px] overflow-hidden border-white/40 bg-[color-mix(in_srgb,var(--primitive-surface)_72%,transparent)] shadow-[var(--shadow-card)] backdrop-blur-[20px] backdrop-saturate-[180%] motion-reduce:animate-none dark:border-white/10";

const FIELD = "h-[var(--control-height-lg)]";

export const Login = () => {
  const {
    signIn,
    signInWithPassword,
    signInWithGoogle,
    startOidcSignIn,
    submitting,
    error,
    authMode,
    devTokenEnabled,
    devTokenEnabledInProd,
    passwordLoginEnabled,
    googleClientId
  } = useAuth();
  const [subject, setSubject] = useState("operator");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<RoleToken>("operator");
  const [passwordVisible, setPasswordVisible] = useState(false);
  const errorRef = useRef<HTMLDivElement | null>(null);
  const roleHintId = useId();
  const signInWithGoogleToken = (idToken: string) => {
    void signInWithGoogle(idToken).catch(() => undefined);
  };

  useEffect(() => {
    if (error) {
      errorRef.current?.focus();
    }
  }, [error]);

  if (authMode === "oidc") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-layout-bg)] p-6">
        <Card className={PANEL}>
          <CardHeader className="items-center gap-3 px-6 pb-2 pt-8 text-center">
            <CryoMark className="size-10" title="Cryo" />
            <div className="flex flex-col gap-1.5">
              <CardTitle className="text-xl font-semibold tracking-[-0.02em]">Sign in to Cryo</CardTitle>
              <p className="text-sm leading-5 text-muted-foreground">Continue with your organization.</p>
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-4 px-6 pb-8">
            {error ? (
              <div ref={errorRef} tabIndex={-1} className="focus-visible:outline-none">
                <Alert
                  variant="error"
                  title="Sign-in failed"
                  description={
                    <>
                      <p>{error}</p>
                      <p className="mt-1">
                        Try again, or contact your administrator if single sign-on keeps failing.
                      </p>
                    </>
                  }
                />
              </div>
            ) : null}
            {googleClientId ? <GoogleSignInButton clientId={googleClientId} onCredential={signInWithGoogleToken} /> : null}
            <Button variant="primary" loading={submitting} className={cn(FIELD, "w-full")} onClick={startOidcSignIn}>
              <Lock aria-hidden="true" />
              Sign in with SSO
            </Button>
            <p className="text-center text-xs leading-4 text-muted-foreground">
              {devTokenEnabledInProd
                ? "Production sign-in uses your organization identity provider. Development sign-in is enabled on this production install."
                : "Production sign-in uses your organization identity provider. Dev token issuance is disabled."}
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const formSignInEnabled = devTokenEnabled || passwordLoginEnabled;

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const typedPassword = document.getElementById("login-password");
    const passwordValue = typedPassword instanceof HTMLInputElement ? typedPassword.value : password;
    if (passwordLoginEnabled && (passwordValue.trim() || !devTokenEnabled)) {
      void signInWithPassword(subject, passwordValue);
      return;
    }
    void signIn(role, subject);
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-layout-bg)] p-6">
      <Card className={PANEL}>
        <CardHeader className="items-center gap-3 px-6 pb-2 pt-8 text-center">
          <CryoMark className="size-10" title="Cryo" />
          <div className="flex flex-col gap-1.5">
            <CardTitle className="text-xl font-semibold tracking-[-0.02em]">Sign in to Cryo</CardTitle>
            <p className="text-sm leading-5 text-muted-foreground">Iceberg maintenance for this datalake.</p>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 px-6 pb-8">
          {googleClientId ? (
            <>
              <GoogleSignInButton clientId={googleClientId} onCredential={signInWithGoogleToken} />
              {formSignInEnabled ? (
                <div className="flex items-center gap-3 text-xs text-muted-foreground" role="separator">
                  <span className="h-px flex-1 bg-border" />
                  or
                  <span className="h-px flex-1 bg-border" />
                </div>
              ) : null}
            </>
          ) : null}
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            {!formSignInEnabled && !googleClientId ? (
              <Alert
                variant="warning"
                title="Dev sign-in is disabled"
                description={
                  <>
                    <p>
                      This deployment requires your organization identity provider. Contact your
                      administrator if you don't have SSO access yet.
                    </p>
                    <details className="mt-1">
                      <summary className="cursor-pointer select-none">Admin details</summary>
                      <p className="mt-1">
                        Configure <code>CRYO_OIDC_ISSUER_URL</code> and <code>CRYO_OIDC_CLIENT_ID</code> to
                        enable production sign-in.
                      </p>
                    </details>
                  </>
                }
              />
            ) : null}
            {error ? (
              <div ref={errorRef} tabIndex={-1} className="focus-visible:outline-none">
                <Alert
                  variant="error"
                  title="Sign-in failed"
                  description={
                    <>
                      <p>{error}</p>
                      <p className="mt-1">{SIGN_IN_RECOVERY_HINT}</p>
                    </>
                  }
                />
              </div>
            ) : null}
            {formSignInEnabled ? (
              <>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="login-subject">Username</Label>
                  <div className="relative">
                    <User
                      className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                      aria-hidden="true"
                    />
                    <Input
                      id="login-subject"
                      className={cn(FIELD, "pl-9")}
                      value={subject}
                      onChange={(event) => setSubject(event.target.value)}
                      placeholder="your.name"
                      autoComplete="username"
                      required
                    />
                  </div>
                </div>
                {passwordLoginEnabled ? (
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="login-password">Password</Label>
                    <div className="relative">
                      <Input
                        id="login-password"
                        className={cn(FIELD, "pr-10")}
                        type={passwordVisible ? "text" : "password"}
                        value={password}
                        onChange={(event) => setPassword(event.target.value)}
                        autoComplete="current-password"
                        placeholder="Password"
                      />
                      <button
                        type="button"
                        className="absolute right-1 top-1/2 inline-flex size-8 -translate-y-1/2 items-center justify-center rounded-[var(--radius-default)] text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-[var(--focus-ring-width)] focus-visible:ring-[var(--focus-ring-color)]"
                        aria-label={passwordVisible ? "Hide password" : "Show password"}
                        aria-pressed={passwordVisible}
                        onClick={() => setPasswordVisible((visible) => !visible)}
                      >
                        {passwordVisible ? <EyeOff className="size-4" aria-hidden="true" /> : <Eye className="size-4" aria-hidden="true" />}
                      </button>
                    </div>
                  </div>
                ) : null}
                {devTokenEnabled ? (
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="login-role">Role</Label>
                    <Segmented
                      block
                      className="h-[var(--control-height-lg)] [&_button]:h-full"
                      value={role}
                      options={roleOptions}
                      onChange={(next) => setRole(next as RoleToken)}
                    />
                    <select
                      id="login-role"
                      tabIndex={-1}
                      className="sr-only"
                      value={role}
                      aria-describedby={passwordLoginEnabled ? roleHintId : undefined}
                      onChange={(event) => setRole(event.target.value as RoleToken)}
                      required
                    >
                      {roleOptions.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                    {passwordLoginEnabled ? (
                      <p id={roleHintId} className="text-xs leading-4 text-muted-foreground">
                        Used only when signing in without a password.
                      </p>
                    ) : null}
                  </div>
                ) : null}
                <Button
                  type="submit"
                  variant="primary"
                  loading={submitting}
                  className={cn(FIELD, "w-full")}
                >
                  <Lock aria-hidden="true" />
                  {passwordLoginEnabled && (password.trim() || !devTokenEnabled) ? "Sign in with password" : "Sign in"}
                </Button>
              </>
            ) : null}
            <p className="text-center text-xs leading-4 text-muted-foreground">
              {devTokenEnabledInProd
                ? "Development sign-in is enabled on this production install. Choose a role and sign in."
                : "Cryo uses signed JWTs from the API. In dev, choose a role and sign in; production deployments disable the token endpoint and integrate your identity provider."}
            </p>
          </form>
        </CardContent>
      </Card>
    </div>
  );
};
