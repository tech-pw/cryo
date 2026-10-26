import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { ConfirmAction } from "../../components/confirm";
import { Alert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Switch } from "../../components/ui/switch";
import { useGoogleIdentityServices } from "../../hooks/useGoogleIdentityServices";
import { useAuth } from "../../lib/auth";
import { formatAgo } from "../home/homeSignals";

interface RoleRow {
  role: string;
  can: string;
}

interface LocalUser {
  id: string;
  username: string;
  role: string;
  disabled: boolean;
  has_password: boolean;
  created_at?: string | null;
  created_via?: string | null;
  last_login_at?: string | null;
  last_login_method?: string | null;
}

interface AuthAuditEntry {
  ts: string;
  action: string;
  actor: string | null;
  detail: string | null;
}

const METHOD_LABELS: Record<string, string> = { google: "Google", password: "password" };

const AUDIT_LABELS: Record<string, string> = {
  "auth.user_created": "Added a person",
  "auth.user_updated": "Changed access",
  "auth.settings_updated": "Changed sign-in settings",
  "auth.login_failed": "Failed sign-in"
};

const lastSignIn = (user: LocalUser): string => {
  const ago = formatAgo(user.last_login_at);
  if (!ago) return "Never signed in";
  const method = user.last_login_method ? METHOD_LABELS[user.last_login_method] : null;
  return method ? `Last sign-in ${ago} with ${method}` : `Last sign-in ${ago}`;
};

interface AuthSettings {
  password_login_enabled: boolean;
  google_enabled: boolean;
  google_client_id: string | null;
  google_hosted_domain: string | null;
  google_auto_provision: boolean;
  google_admin_emails?: string[];
}

interface GoogleTestResult {
  ok: boolean;
  email: string | null;
  message: string;
}

const authFetch = async <T,>(token: string, path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(path, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    }
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    const detail = body?.detail;
    const message =
      typeof detail === "string" ? detail : detail != null ? JSON.stringify(detail) : `Request failed (${response.status})`;
    throw new Error(message);
  }
  return (await response.json()) as T;
};

const CopyField = ({ label, value }: { label: string; value: string }) => {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex flex-col gap-1">
      <Label>{label}</Label>
      <div className="flex items-stretch gap-2">
        <code className="flex-1 overflow-x-auto rounded-[var(--radius-default)] border border-border bg-[var(--color-surface-sunken)] px-3 py-2 text-sm">
          {value}
        </code>
        <Button
          type="button"
          variant="secondary"
          onClick={() => {
            void navigator.clipboard.writeText(value).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            });
          }}
          aria-label={`Copy ${label}`}
        >
          {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
        </Button>
      </div>
    </div>
  );
};

export const AccessControl = () => {
  const { token, role, subject } = useAuth();
  const queryClient = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [userRole, setUserRole] = useState("viewer");
  const [adminEmail, setAdminEmail] = useState("");
  const [clientId, setClientId] = useState<string | null>(null);
  const [domain, setDomain] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<GoogleTestResult | null>(null);
  const [testPassed, setTestPassed] = useState(false);
  const [peopleFilter, setPeopleFilter] = useState("");
  const { status: gsiStatus, loadError: gsiLoadError, requestIdToken } = useGoogleIdentityServices();

  const origin = window.location.origin;

  const roles = useQuery({
    queryKey: ["auth-roles"],
    queryFn: () => authFetch<{ roles: RoleRow[] }>(token, "/api/v1/auth/roles")
  });
  const users = useQuery({
    queryKey: ["auth-users"],
    queryFn: () => authFetch<LocalUser[]>(token, "/api/v1/auth/users"),
    enabled: role === "admin"
  });
  const settings = useQuery({
    queryKey: ["auth-settings"],
    queryFn: () => authFetch<AuthSettings>(token, "/api/v1/auth/settings"),
    enabled: role === "admin"
  });

  const audit = useQuery({
    queryKey: ["auth-audit"],
    queryFn: () => authFetch<AuthAuditEntry[]>(token, "/api/v1/auth/audit?limit=20"),
    enabled: role === "admin"
  });

  const effectiveClientId = clientId ?? settings.data?.google_client_id ?? "";
  const effectiveDomain = domain ?? settings.data?.google_hosted_domain ?? "";

  const refreshPeople = () => {
    void queryClient.invalidateQueries({ queryKey: ["auth-users"] });
    void queryClient.invalidateQueries({ queryKey: ["auth-settings"] });
    void queryClient.invalidateQueries({ queryKey: ["auth-audit"] });
  };

  const createUser = useMutation({
    mutationFn: () =>
      authFetch(token, "/api/v1/auth/users", {
        method: "POST",
        body: JSON.stringify({ username, password: password || null, role: userRole })
      }),
    onSuccess: () => {
      setUsername("");
      setPassword("");
      setError(null);
      refreshPeople();
    },
    onError: (err: Error) => setError(err.message)
  });

  const addGoogleAdmin = useMutation({
    mutationFn: (email: string) => {
      const normalized = email.trim().toLowerCase();
      const existing = users.data?.find((user) => user.username === normalized);
      if (existing) {
        return authFetch(token, `/api/v1/auth/users/${existing.id}`, {
          method: "PATCH",
          body: JSON.stringify({ role: "admin", disabled: false })
        });
      }
      return authFetch(token, "/api/v1/auth/users", {
        method: "POST",
        body: JSON.stringify({ username: normalized, password: null, role: "admin" })
      });
    },
    onSuccess: () => {
      setAdminEmail("");
      setError(null);
      refreshPeople();
    },
    onError: (err: Error) => setError(err.message)
  });

  const patchUser = useMutation({
    mutationFn: (payload: { id: string; role?: string; disabled?: boolean }) =>
      authFetch(token, `/api/v1/auth/users/${payload.id}`, {
        method: "PATCH",
        body: JSON.stringify({ role: payload.role, disabled: payload.disabled })
      }),
    onSuccess: () => {
      setError(null);
      refreshPeople();
    },
    onError: (err: Error) => setError(err.message)
  });

  const googleAdmins = settings.data?.google_admin_emails ?? [];
  const testedEmail = testResult?.ok ? testResult.email : null;
  const testedEmailIsAdmin = testedEmail ? googleAdmins.includes(testedEmail) : false;

  const testGoogle = useMutation({
    mutationFn: async () => {
      const idToken = await requestIdToken(effectiveClientId);
      return authFetch<GoogleTestResult>(token, "/api/v1/auth/settings/google/test", {
        method: "POST",
        body: JSON.stringify({
          id_token: idToken,
          client_id: effectiveClientId,
          hosted_domain: effectiveDomain || null
        })
      });
    },
    onSuccess: (result) => {
      setTestResult(result);
      setTestPassed(result.ok);
    },
    onError: (err: Error) => {
      setTestResult({ ok: false, email: null, message: err.message });
      setTestPassed(false);
    }
  });

  const saveGoogle = useMutation({
    mutationFn: (enabled: boolean) =>
      authFetch(token, "/api/v1/auth/settings", {
        method: "PATCH",
        body: JSON.stringify({
          google_enabled: enabled,
          google_client_id: effectiveClientId,
          google_hosted_domain: effectiveDomain || null,
          password_login_enabled: settings.data?.password_login_enabled ?? true,
          google_auto_provision: effectiveDomain ? (settings.data?.google_auto_provision ?? false) : false
        })
      }),
    onSuccess: () => {
      setError(null);
      refreshPeople();
    },
    onError: (err: Error) => setError(err.message)
  });

  const saveAutoProvision = useMutation({
    mutationFn: (enabled: boolean) =>
      authFetch(token, "/api/v1/auth/settings", {
        method: "PATCH",
        body: JSON.stringify({ google_auto_provision: enabled })
      }),
    onSuccess: () => {
      setError(null);
      refreshPeople();
    },
    onError: (err: Error) => setError(err.message)
  });

  const savedDomain = settings.data?.google_hosted_domain ?? "";
  const filter = peopleFilter.trim().toLowerCase();
  const people = (users.data ?? []).filter((user) => !filter || user.username.includes(filter));

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base font-medium tracking-[-0.01em]">Roles</CardTitle>
          <CardDescription>
            Viewer can look. Operator can run maintenance. Admin can change connections, users, and
            sign-in. A miss by a viewer cannot start a job.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="grid grid-cols-1 gap-2 md:grid-cols-3">
            {(roles.data?.roles ?? []).map((row) => (
              <li
                key={row.role}
                className="min-w-0 rounded-[var(--radius-default)] border border-border bg-[var(--color-surface-sunken)] px-3 py-2"
              >
                <span className="font-medium">{row.role}</span>
                <span className="mt-1 block text-muted-foreground">{row.can}</span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
      {role !== "admin" ? (
        <p className="settings-note text-sm text-muted-foreground">Only an admin can change users or Google sign-in.</p>
      ) : (
        <>
          {error ? <Alert variant="error" title="Could not save" description={error} /> : null}
          <Card>
            <CardHeader>
              <CardTitle className="text-base font-medium tracking-[-0.01em]">People</CardTitle>
            </CardHeader>
            <CardContent>
            <form
              className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-[minmax(12rem,1fr)_minmax(12rem,1fr)_minmax(8rem,0.55fr)_auto]"
              onSubmit={(event) => {
                event.preventDefault();
                createUser.mutate();
              }}
            >
              <div className="min-w-0">
                <Label htmlFor="new-username">Username</Label>
                <Input id="new-username" value={username} onChange={(event) => setUsername(event.target.value)} required />
              </div>
              <div className="min-w-0">
                <Label htmlFor="new-password">Password (optional)</Label>
                <Input
                  id="new-password"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  minLength={8}
                  placeholder="Blank = Google sign-in only"
                  aria-describedby="new-user-hint"
                />
              </div>
              <div className="min-w-0">
                <Label htmlFor="new-role">Role</Label>
                <select
                  id="new-role"
                  value={userRole}
                  onChange={(event) => setUserRole(event.target.value)}
                  className="flex h-[var(--control-height)] w-full min-w-0 max-w-full rounded-[var(--radius-default)] border border-[var(--color-control-border)] bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-[var(--focus-ring-width)] focus-visible:ring-[var(--focus-ring-color)]"
                >
                  <option value="viewer">viewer</option>
                  <option value="operator">operator</option>
                  <option value="admin">admin</option>
                </select>
              </div>
              <Button type="submit" variant="primary" loading={createUser.isPending}>
                Add user
              </Button>
            </form>
            <p id="new-user-hint" className="mt-2 text-xs text-muted-foreground">
              Leave the password blank for someone who signs in with Google, and use their Google email
              as the username.
            </p>
            {(users.data?.length ?? 0) > 5 ? (
              <div className="mt-3 max-w-sm">
                <Label htmlFor="people-filter">Find a person</Label>
                <Input
                  id="people-filter"
                  type="search"
                  value={peopleFilter}
                  onChange={(event) => setPeopleFilter(event.target.value)}
                  placeholder="name@company.com"
                />
              </div>
            ) : null}
            <ul className="mt-3 flex flex-col gap-2" aria-label="People">
              {people.map((user) => {
                const isSelf = user.username === subject.toLowerCase();
                return (
                <li
                  key={user.id}
                  className="flex flex-wrap items-center gap-2 rounded-[var(--radius-default)] border border-border bg-[var(--color-surface-sunken)] px-3 py-2 text-sm"
                >
                  <div className="flex min-w-48 flex-1 flex-col">
                    <span className="font-medium">
                      {user.username}
                      {isSelf ? <span className="ml-1 font-normal text-muted-foreground">(you)</span> : null}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {lastSignIn(user)}
                      {user.created_via === "google" ? " · Joined with Google" : ""}
                    </span>
                  </div>
                  {user.disabled ? <Badge variant="warning">Disabled</Badge> : null}
                  <Badge variant="secondary">
                    {user.has_password
                      ? user.username.includes("@")
                        ? "Password or Google"
                        : "Password"
                      : "Google"}
                  </Badge>
                  <select
                    aria-label={`Role for ${user.username}`}
                    value={user.role}
                    onChange={(event) => patchUser.mutate({ id: user.id, role: event.target.value })}
                    disabled={isSelf}
                    title={isSelf ? "Ask another admin to change your role." : undefined}
                    className="h-[var(--control-height)] w-auto min-w-0 max-w-full rounded-[var(--radius-default)] border border-[var(--color-control-border)] bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-[var(--focus-ring-width)] focus-visible:ring-[var(--focus-ring-color)]"
                  >
                    <option value="viewer">viewer</option>
                    <option value="operator">operator</option>
                    <option value="admin">admin</option>
                  </select>
                  {user.disabled ? (
                    <Button
                      type="button"
                      variant="secondary"
                      loading={patchUser.isPending && patchUser.variables?.id === user.id}
                      disabled={patchUser.isPending && patchUser.variables?.id === user.id}
                      onClick={() => patchUser.mutate({ id: user.id, disabled: false })}
                    >
                      Enable
                    </Button>
                  ) : (
                    <ConfirmAction
                      title={`Disable ${user.username}?`}
                      description={`${user.username} is signed out of Cryo and can't sign in again until an admin enables the account.`}
                      okText="Disable user"
                      okButtonProps={{ danger: true }}
                      onConfirm={async () => {
                        try {
                          await patchUser.mutateAsync({ id: user.id, disabled: true });
                        } catch {
                          // onError already shows the message above the list.
                        }
                      }}
                      disabled={isSelf}
                    >
                      <Button
                        type="button"
                        variant="secondary"
                        loading={patchUser.isPending && patchUser.variables?.id === user.id}
                        disabled={isSelf || (patchUser.isPending && patchUser.variables?.id === user.id)}
                      >
                        Disable
                      </Button>
                    </ConfirmAction>
                  )}
                </li>
                );
              })}
              {users.data && people.length === 0 ? (
                <li className="text-sm text-muted-foreground">No one matches &quot;{peopleFilter}&quot;.</li>
              ) : null}
            </ul>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <CardTitle className="text-base font-medium tracking-[-0.01em]">Google sign-in</CardTitle>
                {settings.data?.google_enabled ? (
                  <Badge variant="success">Enabled</Badge>
                ) : (
                  <Badge variant="secondary">Disabled</Badge>
                )}
              </div>
              <CardDescription>
                Paste the Google Client ID. No client secret is stored. Choose below whether new
                Google accounts on your domain get viewer access on their first sign-in, or only
                people you add can sign in.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
            <div className="rounded-[var(--radius-default)] border border-border bg-[var(--color-surface-sunken)] p-4">
              <h3 className="text-sm font-medium">1. Create an OAuth Client ID in Google Cloud Console</h3>
              <ol className="mt-2 flex list-decimal flex-col gap-1 pl-5 text-sm text-muted-foreground">
                <li>
                  Open{" "}
                  <a
                    href="https://console.cloud.google.com/apis/credentials"
                    target="_blank"
                    rel="noreferrer"
                    className="text-[var(--color-primary)] underline underline-offset-2"
                  >
                    APIs &amp; Services → Credentials
                  </a>{" "}
                  in your Google Cloud project (create one first if you don&apos;t have one).
                </li>
                <li>Click &quot;Create credentials&quot; → &quot;OAuth client ID&quot; → application type &quot;Web application&quot;.</li>
                <li>Paste the two values below into <strong>Authorized JavaScript origins</strong> and <strong>Authorized redirect URIs</strong> exactly as shown.</li>
                <li>Save, then copy the generated Client ID into the field below.</li>
              </ol>
              <div className="mt-3 flex flex-col gap-3">
                <CopyField label="Authorized JavaScript origin" value={origin} />
                <CopyField label="Authorized redirect URI" value={`${origin}/auth/google/callback`} />
              </div>
              <p className="mt-3 text-xs text-muted-foreground">
                Google Identity Services sign-in (the popup flow used here) only checks the
                JavaScript origin, not the redirect URI — add the redirect URI anyway so this
                Client ID is ready if the sign-in method ever changes to a redirect-based flow.
              </p>
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="google-client-id">2. Client ID</Label>
              <Input
                id="google-client-id"
                value={effectiveClientId}
                onChange={(event) => {
                  setClientId(event.target.value);
                  setTestResult(null);
                  setTestPassed(false);
                }}
                placeholder="….apps.googleusercontent.com"
              />
              <Label htmlFor="google-domain">Restrict to domain (optional)</Label>
              <Input
                id="google-domain"
                value={effectiveDomain}
                onChange={(event) => {
                  setDomain(event.target.value);
                  setTestResult(null);
                  setTestPassed(false);
                }}
                placeholder="company.com"
              />
              <p className="text-xs text-muted-foreground">
                If set, only Google accounts on this domain can sign in. Leave blank to allow any
                Google account that you add as a person.
              </p>

              {gsiStatus === "error" ? (
                <Alert
                  variant="error"
                  title="Google sign-in script failed to load"
                  description={gsiLoadError ?? "Check your network connection and reload this page."}
                />
              ) : null}

              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  loading={testGoogle.isPending}
                  disabled={!effectiveClientId || gsiStatus !== "ready"}
                  onClick={() => testGoogle.mutate()}
                >
                  Test configuration
                </Button>
                <Button
                  type="button"
                  variant="primary"
                  loading={saveGoogle.isPending}
                  disabled={!testPassed}
                  onClick={() => saveGoogle.mutate(true)}
                >
                  Enable Google sign-in
                </Button>
                {settings.data?.google_enabled ? (
                  <Button
                    type="button"
                    variant="secondary"
                    loading={saveGoogle.isPending}
                    onClick={() => saveGoogle.mutate(false)}
                  >
                    Disable
                  </Button>
                ) : null}
              </div>
              <p className="text-xs text-muted-foreground">
                Test opens a real Google sign-in popup against this Client ID and verifies the
                result on the server before anything is saved — &quot;Enable&quot; stays off until a test
                passes.
              </p>

              {testResult ? (
                <Alert
                  variant={testResult.ok ? "success" : "error"}
                  title={testResult.ok ? "Test passed" : "Test failed"}
                  description={testResult.message}
                />
              ) : null}
              {testedEmail && !testedEmailIsAdmin ? (
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="text-muted-foreground">{testedEmail} is not a Cryo admin yet.</span>
                  <Button
                    type="button"
                    variant="secondary"
                    loading={addGoogleAdmin.isPending}
                    onClick={() => addGoogleAdmin.mutate(testedEmail)}
                  >
                    Add {testedEmail} as admin
                  </Button>
                </div>
              ) : null}
            </div>

            <div className="flex flex-col gap-2">
              <h3 className="text-sm font-medium">3. Who can sign in</h3>
              <div className="flex items-start gap-3">
                <Switch
                  id="google-auto-provision"
                  checked={Boolean(settings.data?.google_auto_provision)}
                  disabled={!savedDomain || saveAutoProvision.isPending}
                  onCheckedChange={(checked) => saveAutoProvision.mutate(checked)}
                  aria-describedby="google-auto-provision-hint"
                />
                <div className="flex flex-col gap-0.5">
                  <Label htmlFor="google-auto-provision">Give new Google accounts viewer access</Label>
                  <p id="google-auto-provision-hint" className="text-xs text-muted-foreground">
                    {savedDomain
                      ? `Anyone on ${savedDomain} can sign in and look around. Raise their role under People.`
                      : "Save a domain restriction first. Without one, any Google account in the world could sign in."}
                  </p>
                </div>
              </div>
            </div>

            <div className="flex flex-col gap-2">
              <h3 className="text-sm font-medium">4. Admins who sign in with Google</h3>
              <p className="text-xs text-muted-foreground">
                Add at least one admin by Google email before turning off dev sign-in, or nobody will
                be able to manage Cryo.
              </p>
              {googleAdmins.length ? (
                <ul className="flex flex-wrap gap-2" aria-label="Google admins">
                  {googleAdmins.map((email) => (
                    <li key={email}>
                      <Badge variant="outline">{email}</Badge>
                    </li>
                  ))}
                </ul>
              ) : settings.data ? (
                <Alert
                  variant="warning"
                  title="No admin can sign in with Google yet"
                  description={
                    effectiveDomain
                      ? `Add an admin with an @${effectiveDomain.replace(/^@/, "")} Google email.`
                      : "Add an admin by Google email."
                  }
                />
              ) : null}
              <form
                className="flex flex-wrap items-end gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  addGoogleAdmin.mutate(adminEmail);
                }}
              >
                <div className="min-w-64 flex-1">
                  <Label htmlFor="google-admin-email">Admin Google email</Label>
                  <Input
                    id="google-admin-email"
                    type="email"
                    value={adminEmail}
                    onChange={(event) => setAdminEmail(event.target.value)}
                    placeholder={effectiveDomain ? `name@${effectiveDomain.replace(/^@/, "")}` : "name@company.com"}
                    required
                  />
                </div>
                <Button type="submit" variant="secondary" loading={addGoogleAdmin.isPending}>
                  Add as admin
                </Button>
              </form>
            </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base font-medium tracking-[-0.01em]">Recent access changes</CardTitle>
              <CardDescription>Who added people, changed roles, or changed sign-in settings.</CardDescription>
            </CardHeader>
            <CardContent>
              {audit.data?.length ? (
                <ul className="flex flex-col gap-2 text-sm" aria-label="Recent access changes">
                  {audit.data.map((entry) => (
                    <li key={`${entry.ts}-${entry.action}-${entry.detail ?? ""}`} className="flex flex-wrap gap-x-2">
                      <span className="font-medium">{AUDIT_LABELS[entry.action] ?? entry.action}</span>
                      {entry.detail ? <span className="text-muted-foreground">{entry.detail}</span> : null}
                      <span className="text-xs text-muted-foreground">
                        {entry.actor ? `by ${entry.actor} · ` : ""}
                        {formatAgo(entry.ts) ?? ""}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">
                  {audit.isLoading ? "Loading…" : "No access changes recorded yet."}
                </p>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
};
