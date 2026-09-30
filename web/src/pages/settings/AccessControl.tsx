import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Alert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { useAuth } from "../../lib/auth";

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
}

interface AuthSettings {
  password_login_enabled: boolean;
  google_enabled: boolean;
  google_client_id: string | null;
  google_hosted_domain: string | null;
  google_auto_provision: boolean;
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
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `Request failed (${response.status})`);
  }
  return (await response.json()) as T;
};

export const AccessControl = () => {
  const { token, role } = useAuth();
  const queryClient = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [userRole, setUserRole] = useState("viewer");
  const [clientId, setClientId] = useState("");
  const [domain, setDomain] = useState("");
  const [error, setError] = useState<string | null>(null);

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

  const createUser = useMutation({
    mutationFn: () =>
      authFetch(token, "/api/v1/auth/users", {
        method: "POST",
        body: JSON.stringify({ username, password, role: userRole })
      }),
    onSuccess: () => {
      setUsername("");
      setPassword("");
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["auth-users"] });
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
      void queryClient.invalidateQueries({ queryKey: ["auth-users"] });
    },
    onError: (err: Error) => setError(err.message)
  });

  const saveGoogle = useMutation({
    mutationFn: (enabled: boolean) =>
      authFetch(token, "/api/v1/auth/settings", {
        method: "PATCH",
        body: JSON.stringify({
          google_enabled: enabled,
          google_client_id: clientId || settings.data?.google_client_id,
          google_hosted_domain: domain || null,
          password_login_enabled: settings.data?.password_login_enabled ?? true,
          google_auto_provision: false
        })
      }),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["auth-settings"] });
    },
    onError: (err: Error) => setError(err.message)
  });

  return (
    <div className="flex flex-col gap-6">
      <section>
        <h2 className="text-base font-medium">Roles</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Viewer can look. Operator can run maintenance. Admin can change connections, users, and
          sign-in. A miss by a viewer cannot start a job.
        </p>
        <ul className="mt-3 flex flex-col gap-2">
          {(roles.data?.roles ?? []).map((row) => (
            <li key={row.role} className="rounded-[var(--radius-default)] border border-border px-3 py-2">
              <span className="font-medium">{row.role}</span>
              <span className="mt-1 block text-muted-foreground">{row.can}</span>
            </li>
          ))}
        </ul>
      </section>
      {role !== "admin" ? (
        <p className="text-sm text-muted-foreground">Only an admin can change users or Google sign-in.</p>
      ) : (
        <>
          {error ? <Alert variant="error" title="Could not save" description={error} /> : null}
          <section>
            <h2 className="text-base font-medium">People</h2>
            <form
              className="mt-3 flex flex-wrap items-end gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                createUser.mutate();
              }}
            >
              <div>
                <Label htmlFor="new-username">Username</Label>
                <Input id="new-username" value={username} onChange={(event) => setUsername(event.target.value)} required />
              </div>
              <div>
                <Label htmlFor="new-password">Password</Label>
                <Input
                  id="new-password"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  minLength={8}
                  required
                />
              </div>
              <div>
                <Label htmlFor="new-role">Role</Label>
                <select
                  id="new-role"
                  value={userRole}
                  onChange={(event) => setUserRole(event.target.value)}
                  className="flex h-[var(--control-height)] rounded-[var(--radius-default)] border border-border bg-background px-3 text-sm"
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
            <ul className="mt-3 flex flex-col gap-2">
              {(users.data ?? []).map((user) => (
                <li key={user.id} className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="min-w-40">{user.username}</span>
                  <select
                    aria-label={`Role for ${user.username}`}
                    value={user.role}
                    onChange={(event) => patchUser.mutate({ id: user.id, role: event.target.value })}
                    className="rounded-[var(--radius-default)] border border-border bg-background px-2 py-1"
                  >
                    <option value="viewer">viewer</option>
                    <option value="operator">operator</option>
                    <option value="admin">admin</option>
                  </select>
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => patchUser.mutate({ id: user.id, disabled: !user.disabled })}
                  >
                    {user.disabled ? "Enable" : "Disable"}
                  </Button>
                </li>
              ))}
            </ul>
          </section>
          <section>
            <h2 className="text-base font-medium">Google sign-in</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Same shape as Metabase: paste the Google Client ID. No client secret is stored. New
              Google accounts are not created unless you add them here. Auto-provision stays off.
            </p>
            <div className="mt-3 flex flex-col gap-2">
              <Label htmlFor="google-client-id">Client ID</Label>
              <Input
                id="google-client-id"
                value={clientId || settings.data?.google_client_id || ""}
                onChange={(event) => setClientId(event.target.value)}
                placeholder="….apps.googleusercontent.com"
              />
              <Label htmlFor="google-domain">Allowed domain</Label>
              <Input
                id="google-domain"
                value={domain || settings.data?.google_hosted_domain || ""}
                onChange={(event) => setDomain(event.target.value)}
                placeholder="company.com"
              />
              <Button
                type="button"
                variant="primary"
                loading={saveGoogle.isPending}
                onClick={() => saveGoogle.mutate(true)}
              >
                Enable Google sign-in
              </Button>
            </div>
          </section>
        </>
      )}
    </div>
  );
};
