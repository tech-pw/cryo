import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { ColumnDef } from "@tanstack/react-table";
import {
  assistantErrorMessage,
  createAssistantApiKey,
  deleteAssistantApiKey,
  formatAssistantCell,
  getAssistantSettings,
  getAssistantUsage,
  listAssistantApiKeys,
  putAssistantSettings,
  revealedApiKey,
  usageRows,
  type AssistantSettings,
  type AssistantUsageRow
} from "../../api/assistant";
import { useAuth } from "../../lib/auth";
import { formatTime } from "../../lib/format";
import { Alert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { DataTable } from "../../components/ui/data-table";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { PageHeader } from "../../components/ui/page-header";
import { Select } from "../../components/ui/select";
import { Switch } from "../../components/ui/switch";
import { Textarea } from "../../components/ui/textarea";

const TIERS = [0, 1, 2, 3];

export const AiModelsSettings = () => {
  const { api, token, role } = useAuth();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<AssistantSettings | null>(null);
  const [keyName, setKeyName] = useState("");
  const [secret, setSecret] = useState<string | null>(null);

  const settings = useQuery({
    queryKey: ["assistant", "settings", token],
    queryFn: () => getAssistantSettings(api),
    enabled: role === "admin",
    retry: false
  });
  const usage = useQuery({
    queryKey: ["assistant", "usage", token],
    queryFn: () => getAssistantUsage(api),
    enabled: role === "admin",
    retry: false
  });
  const keys = useQuery({
    queryKey: ["assistant", "api-keys", token],
    queryFn: () => listAssistantApiKeys(api),
    enabled: role === "admin",
    retry: false
  });

  useEffect(() => {
    if (settings.data) setDraft(settings.data);
  }, [settings.data]);

  const save = useMutation({
    mutationFn: (body: AssistantSettings) => putAssistantSettings(api, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["assistant", "settings"] });
      void queryClient.invalidateQueries({ queryKey: ["assistant", "capabilities"] });
    }
  });
  const createKey = useMutation({
    mutationFn: () => createAssistantApiKey(api, { name: keyName.trim() }),
    onSuccess: (data) => {
      setSecret(revealedApiKey(data));
      setKeyName("");
      void queryClient.invalidateQueries({ queryKey: ["assistant", "api-keys"] });
    }
  });
  const revoke = useMutation({
    mutationFn: (id: string) => deleteAssistantApiKey(api, id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["assistant", "api-keys"] })
  });

  const rows = useMemo(() => usageRows(usage.data), [usage.data]);
  const usageColumns = useMemo<ColumnDef<AssistantUsageRow, unknown>[]>(
    () => [
      {
        id: "key",
        header: "Day",
        cell: ({ row }) => formatAssistantCell(row.original.key ?? row.original.day ?? row.original.subject)
      },
      { id: "turns", header: "Turns", cell: ({ row }) => formatAssistantCell(row.original.turns) },
      {
        id: "input_tokens",
        header: "Input tokens",
        cell: ({ row }) => formatAssistantCell(row.original.input_tokens ?? row.original.tokens)
      },
      { id: "output_tokens", header: "Output tokens", cell: ({ row }) => formatAssistantCell(row.original.output_tokens) },
      { id: "cost_usd", header: "Cost", cell: ({ row }) => formatAssistantCell(row.original.cost_usd) }
    ],
    []
  );

  if (role !== "admin") {
    return (
      <PageHeader
        title="AI models"
        subtitle="Admin role is required to change Ask Cryo model settings."
      />
    );
  }

  const tiers = new Set(draft?.enabled_tiers ?? []);

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader
        title="AI models"
        subtitle="Admin only. These settings apply to Ask Cryo for the whole install."
        actions={
          <Link to="/settings/ai" className="text-sm text-primary underline-offset-2 hover:underline">
            AI settings
          </Link>
        }
      />
      <Alert
        variant="info"
        title="What is sent to the provider"
        description="Table names, stats and error text are sent to the configured provider. Row values are not sent unless send row values is on."
      />
      {settings.isError ? <Alert variant="error" title="Could not load settings" description={assistantErrorMessage(settings.error)} /> : null}
      {draft ? (
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate(draft);
          }}
        >
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="ask-enabled">Ask Cryo enabled</Label>
            <Switch id="ask-enabled" checked={draft.enabled} onCheckedChange={(checked) => setDraft({ ...draft, enabled: checked })} />
          </div>
          <div>
            <Label htmlFor="ask-role">Allowed role</Label>
            <Select
              id="ask-role"
              className="mt-1.5"
              value={draft.allowed_min_role}
              options={[
                { value: "viewer", label: "Viewer" },
                { value: "operator", label: "Operator" },
                { value: "admin", label: "Admin" }
              ]}
              onChange={(value) => setDraft({ ...draft, allowed_min_role: value ?? "viewer" })}
            />
          </div>
          <fieldset className="space-y-2">
            <legend className="text-xs font-medium">Enabled tiers</legend>
            {TIERS.map((tier) => (
              <label key={tier} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={tiers.has(tier)}
                  onChange={(event) => {
                    const next = new Set(draft.enabled_tiers);
                    if (event.target.checked) next.add(tier);
                    else next.delete(tier);
                    setDraft({ ...draft, enabled_tiers: [...next].sort((a, b) => a - b) });
                  }}
                />
                Tier {tier}
              </label>
            ))}
          </fieldset>
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="ask-rows">Send row values</Label>
            <Switch
              id="ask-rows"
              checked={draft.send_row_values}
              onCheckedChange={(checked) => setDraft({ ...draft, send_row_values: checked })}
            />
          </div>
          <div>
            <Label htmlFor="ask-instructions">Team instructions</Label>
            <Textarea
              id="ask-instructions"
              className="mt-1.5"
              rows={5}
              value={draft.team_instructions_skillset ?? ""}
              onChange={(event) => setDraft({ ...draft, team_instructions_skillset: event.target.value })}
            />
          </div>
          {save.isError ? <Alert variant="error" title="Save failed" description={assistantErrorMessage(save.error)} /> : null}
          <Button type="submit" variant="primary" loading={save.isPending}>
            Save
          </Button>
        </form>
      ) : null}
      <section className="space-y-2">
        <h2 className="text-sm font-semibold">Usage</h2>
        <DataTable
          columns={usageColumns}
          data={rows}
          density="compact"
          loading={usage.isLoading}
          getRowId={(row, index) => String(row.id ?? `${row.subject ?? "row"}-${row.day ?? index}`)}
          emptyTitle="No usage"
          emptyDescription="No Ask Cryo usage has been recorded."
        />
      </section>
      <section className="space-y-3">
        <h2 className="text-sm font-semibold">API keys</h2>
        {secret ? (
          <Alert
            variant="warning"
            title="Copy this key now. It will not be shown again."
            description={<span className="break-all font-mono">{secret}</span>}
            onDismiss={() => setSecret(null)}
          />
        ) : null}
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (keyName.trim()) createKey.mutate();
          }}
        >
          <div className="min-w-[16rem] flex-1">
            <Label htmlFor="ask-key-name">Key name</Label>
            <Input id="ask-key-name" className="mt-1.5" value={keyName} onChange={(event) => setKeyName(event.target.value)} />
          </div>
          <Button type="submit" variant="secondary" loading={createKey.isPending} disabled={!keyName.trim()}>
            Create key
          </Button>
        </form>
        {createKey.isError ? <Alert variant="error" title="Could not create key" description={assistantErrorMessage(createKey.error)} /> : null}
        <ul className="space-y-2">
          {(keys.data ?? []).map((item) => (
            <li key={item.id} className="flex items-center justify-between gap-3 rounded-[var(--radius-default)] border border-border px-3 py-2 text-sm">
              <span>
                {item.name}
                <span className="ml-2 text-xs text-muted-foreground">
                  {item.revoked_at ? `Revoked ${formatTime(item.revoked_at)}` : formatTime(item.created_at)}
                </span>
              </span>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={Boolean(item.revoked_at)}
                loading={revoke.isPending && revoke.variables === item.id}
                onClick={() => revoke.mutate(item.id)}
              >
                Revoke
              </Button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
};
