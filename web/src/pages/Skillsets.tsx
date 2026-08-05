import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { useMemo, useState } from "react";
import { z } from "zod";
import { toast } from "sonner";
import {
  activateSkillset,
  createSkillset,
  listLlmObservations,
  listSkillsets,
  promoteSkillset,
  rollbackSkillset
} from "../api/client";
import { useAuth } from "../lib/auth";
import type { SkillsetRead } from "../api/types";
import { ConfirmAction } from "../components/confirm";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { DataTable } from "../components/ui/data-table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from "../components/ui/dialog";
import { EmptyState } from "../components/ui/empty-state";
import { FormField, FormProvider, useZodForm, zodResolver } from "../components/ui/form";
import { FormErrorSummary } from "../components/ui/form-error-summary";
import { Input } from "../components/ui/input";
import { PageHeader } from "../components/ui/page-header";
import { Select } from "../components/ui/select";
import { Separator } from "../components/ui/separator";
import { Skeleton } from "../components/ui/skeleton";
import { Textarea } from "../components/ui/textarea";
import { Checkbox } from "../components/ui/checkbox";
import { Label } from "../components/ui/label";

const promoteSchema = z.object({
  target_version: z.string().regex(/^\d+\.\d+\.\d+$/, "Use semver format, e.g. 1.2.0")
});

const createSchema = z.object({
  name: z.string().min(1, "Enter a skillset name"),
  version: z.string().regex(/^\d+\.\d+\.\d+$/, "Use semver format, e.g. 1.2.0"),
  prompts: z
    .string()
    .min(1)
    .refine((value) => {
      try {
        const parsed = JSON.parse(value);
        return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed);
      } catch {
        return false;
      }
    }, "Prompts must be a JSON object"),
  tools: z
    .string()
    .min(1)
    .refine((value) => {
      try {
        return Array.isArray(JSON.parse(value));
      } catch {
        return false;
      }
    }, "Tools must be a JSON array"),
  activate: z.boolean()
});

type CreateFormValues = z.infer<typeof createSchema>;

const DEFAULT_CREATE_VALUES: CreateFormValues = {
  name: "iceberg_advisor",
  version: "",
  prompts: '{\n  "table_insight": "",\n  "plan_explanation": "",\n  "savings_forecast": ""\n}',
  tools: "[]",
  activate: false
};

/** The three prompt keys the built-in `iceberg_advisor` skillset actually seeds
 * (`cryo/ai/skillset.py::DEFAULT_ICEBERG_ADVISOR_SKILLSET`), each with the exact `{variable}`
 * placeholder the backend fills via `str.format(**variables)`. These are the only prompt names
 * promoted to structured fields — anything else stays inside the advanced JSON editor so we never
 * invent schema the backend doesn't have. */
const KNOWN_PROMPT_FIELDS: { key: string; label: string; hint: string }[] = [
  {
    key: "table_insight",
    label: "Table insight",
    hint: "Used when summarizing a scanned table's health. Available variable: {stats}"
  },
  {
    key: "plan_explanation",
    label: "Plan explanation",
    hint: "Used when explaining a maintenance plan in plain English. Available variable: {plan}"
  },
  {
    key: "savings_forecast",
    label: "Savings forecast",
    hint: "Used when summarizing a cost/savings forecast. Available variable: {forecast}"
  }
];

const parsePromptsSafe = (value: string): Record<string, string> => {
  try {
    const parsed: unknown = JSON.parse(value);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, string>;
    }
  } catch {
    // Leave the caller with an empty object when the JSON isn't parseable yet — the raw text
    // itself is still what's submitted, this is only used for the structured-field preview.
  }
  return {};
};

/** Extracts the `{name}` placeholders a template references, in first-seen order, treating
 * doubled braces (`{{`/`}}`) as Python `str.format` escapes rather than variables — matching
 * `Skillset.prompt()`'s own `template.format(**variables)` semantics exactly. */
const extractTemplateVariables = (template: string): string[] => {
  const seen = new Set<string>();
  const result: string[] = [];
  const re = /\{\{|\}\}|\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(template)) !== null) {
    const name = match[1];
    if (name && !seen.has(name)) {
      seen.add(name);
      result.push(name);
    }
  }
  return result;
};

interface TemplatePreviewResult {
  rendered: string;
  missing: string[];
}

/** Deterministic, entirely client-side substitution — no model/API call. Mirrors the backend's
 * exact fallback: if any referenced variable is missing, the original unrendered template is
 * returned unchanged (Python's `.format(**variables)` raises `KeyError` and `Skillset.prompt()`
 * catches it by returning the raw template). */
const renderTemplatePreview = (template: string, variables: Record<string, string>): TemplatePreviewResult => {
  const required = extractTemplateVariables(template);
  const missing = required.filter((key) => !(variables[key] ?? "").trim());
  if (missing.length > 0) {
    return { rendered: template, missing };
  }
  let rendered = template;
  for (const key of required) {
    rendered = rendered.split(`{${key}}`).join(variables[key] ?? "");
  }
  return { rendered: rendered.replace(/\{\{/g, "{").replace(/\}\}/g, "}"), missing: [] };
};

interface PromptsEditorProps {
  value: string;
  onChange: (next: string) => void;
  error?: string;
}

/** Structured-fields-by-default, advanced-JSON-for-experts pattern: the three known prompt keys
 * get real labelled textareas; anything else in the object (custom skillsets can use any prompt
 * name) is preserved untouched and only reachable via the raw JSON view. */
const PromptsEditor = ({ value, onChange, error }: PromptsEditorProps) => {
  const [advanced, setAdvanced] = useState(false);
  const parsed = useMemo(() => parsePromptsSafe(value), [value]);
  const extraKeys = Object.keys(parsed).filter((key) => !KNOWN_PROMPT_FIELDS.some((f) => f.key === key));

  const setPromptKey = (key: string, next: string) => {
    onChange(JSON.stringify({ ...parsed, [key]: next }, null, 2));
  };

  return (
    <div className="space-y-3">
      {advanced ? (
        <div>
          <Label htmlFor="field-prompts-json">Prompts (advanced JSON)</Label>
          <Textarea
            id="field-prompts-json"
            rows={8}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className="mono-textarea mt-1.5 font-mono text-xs"
            spellCheck={false}
            aria-invalid={error ? true : undefined}
          />
        </div>
      ) : (
        <div className="space-y-3">
          {KNOWN_PROMPT_FIELDS.map((f) => (
            <div key={f.key}>
              <Label htmlFor={`field-prompt-${f.key}`}>{f.label}</Label>
              <p className="mb-1 text-xs text-muted-foreground">{f.hint}</p>
              <Textarea
                id={`field-prompt-${f.key}`}
                rows={3}
                value={parsed[f.key] ?? ""}
                onChange={(e) => setPromptKey(f.key, e.target.value)}
                className="mono-textarea font-mono text-xs"
                spellCheck={false}
              />
            </div>
          ))}
          {extraKeys.length > 0 ? (
            <p className="text-xs text-muted-foreground">
              This candidate also has custom prompt key{extraKeys.length === 1 ? "" : "s"} ({extraKeys.join(", ")})
              not shown here — switch to advanced JSON to edit them.
            </p>
          ) : null}
        </div>
      )}
      {error ? (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      ) : null}
      <button
        type="button"
        className="text-xs font-medium text-muted-foreground underline-offset-2 hover:underline"
        onClick={() => setAdvanced(!advanced)}
      >
        {advanced ? "Use structured prompt fields" : "Advanced: edit raw JSON"}
      </button>
    </div>
  );
};

/** Safe client-side preview/validation harness: lets an author fill in sample values for a
 * template's `{variable}` placeholders and see the exact deterministic substitution the backend
 * would produce — without calling any AI provider. Purely a template-rendering check. */
const PromptPreviewHarness = ({ promptsJson }: { promptsJson: string }) => {
  const parsed = useMemo(() => parsePromptsSafe(promptsJson), [promptsJson]);
  const keys = Object.keys(parsed).filter((key) => parsed[key]);
  const [selectedKey, setSelectedKey] = useState<string>("");
  const [sampleValues, setSampleValues] = useState<Record<string, string>>({});

  const activeKey = keys.includes(selectedKey) ? selectedKey : keys[0] ?? "";
  const template = activeKey ? parsed[activeKey] ?? "" : "";
  const variables = useMemo(() => extractTemplateVariables(template), [template]);
  const result = useMemo(() => renderTemplatePreview(template, sampleValues), [template, sampleValues]);

  if (keys.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        Add at least one non-empty prompt template above to preview its rendered output.
      </p>
    );
  }

  return (
    <div className="space-y-3 rounded-[var(--radius-default)] border p-3">
      <div>
        <Label htmlFor="field-preview-prompt">Preview a prompt template</Label>
        <Select
          id="field-preview-prompt"
          className="mt-1.5"
          value={activeKey}
          onChange={(next) => setSelectedKey(next ?? "")}
          options={keys.map((key) => ({ value: key, label: key }))}
        />
      </div>
      {variables.length > 0 ? (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {variables.map((name) => (
            <div key={name}>
              <Label htmlFor={`field-preview-var-${name}`}>{`{${name}}`}</Label>
              <Input
                id={`field-preview-var-${name}`}
                className="mt-1.5"
                placeholder={`Sample value for ${name}`}
                value={sampleValues[name] ?? ""}
                onChange={(e) => setSampleValues((prev) => ({ ...prev, [name]: e.target.value }))}
              />
            </div>
          ))}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">This template has no {"{variable}"} placeholders.</p>
      )}
      <div>
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {result.missing.length > 0 ? "Unrendered (fill every variable above to preview)" : "Rendered preview"}
        </p>
        <pre className="mt-1 whitespace-pre-wrap rounded-[var(--radius-default)] bg-muted p-2 text-xs">
          {result.rendered || "(empty template)"}
        </pre>
      </div>
      <p className="text-xs text-muted-foreground">
        This is a local, deterministic text substitution for validation only — it does not call any AI provider.
      </p>
    </div>
  );
};

/** Real, honest lifecycle context for an activate/rollback confirmation — only fields the backend
 * actually returns (`name`, `version`, `is_active`). No invented "affected consumers" or "audit
 * actor" copy; recovery is described exactly as the backend behaves (`activate`/`rollback` are
 * the same underlying operation, so any version can be re-activated afterward). */
const LifecycleChangeSummary = ({
  action,
  target,
  currentlyActive
}: {
  action: "activate" | "rollback";
  target: SkillsetRead;
  currentlyActive: SkillsetRead | undefined;
}) => (
  <div className="space-y-2 text-sm">
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
      <dt className="text-muted-foreground">Skillset</dt>
      <dd className="font-medium">{target.name}</dd>
      <dt className="text-muted-foreground">Currently active version</dt>
      <dd className="font-medium">{currentlyActive ? currentlyActive.version : "None currently active"}</dd>
      <dt className="text-muted-foreground">{action === "rollback" ? "Version to roll back to" : "Version to activate"}</dt>
      <dd className="font-medium">{target.version}</dd>
    </dl>
    <p className="text-muted-foreground">
      {currentlyActive
        ? `Version ${currentlyActive.version} will be marked inactive; version ${target.version} becomes the one served immediately.`
        : `Version ${target.version} becomes the one served immediately.`}{" "}
      This does not delete any version — you can activate a different version again at any time to undo it.
    </p>
  </div>
);

export const Skillsets = () => {
  const { api, role } = useAuth();
  const queryClient = useQueryClient();
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [source, setSource] = useState<SkillsetRead | null>(null);

  const promoteForm = useZodForm(promoteSchema);
  const createForm = useZodForm(createSchema, { defaultValues: DEFAULT_CREATE_VALUES });

  const query = useQuery({
    queryKey: ["skillsets"],
    queryFn: () => listSkillsets(api)
  });
  const observations = useQuery({
    queryKey: ["llm-observations", "skillsets"],
    queryFn: () => listLlmObservations(api, { skillset_name: "iceberg_advisor", limit: 100 })
  });

  const activateMutation = useMutation({
    mutationFn: (row: SkillsetRead) => activateSkillset(api, row.name, { version: row.version }),
    onSuccess: () => {
      toast.success("Activated skillset version");
      void queryClient.invalidateQueries({ queryKey: ["skillsets"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });
  const rollbackMutation = useMutation({
    mutationFn: (row: SkillsetRead) => rollbackSkillset(api, row.name, { version: row.version }),
    onSuccess: () => {
      toast.success("Rollback applied");
      void queryClient.invalidateQueries({ queryKey: ["skillsets"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });
  const promoteMutation = useMutation({
    mutationFn: (args: { name: string; sourceVersion: string; targetVersion: string }) =>
      promoteSkillset(api, args.name, {
        source_version: args.sourceVersion,
        target_version: args.targetVersion,
        activate: true
      }),
    onSuccess: () => {
      toast.success("Candidate promoted and activated");
      setPromoteOpen(false);
      promoteForm.reset();
      void queryClient.invalidateQueries({ queryKey: ["skillsets"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });
  const createMutation = useMutation({
    mutationFn: (values: CreateFormValues) =>
      createSkillset(api, {
        name: values.name.trim(),
        version: values.version.trim(),
        prompts: JSON.parse(values.prompts),
        tools: JSON.parse(values.tools),
        activate: values.activate
      }),
    onSuccess: () => {
      toast.success("Skillset candidate created");
      setCreateOpen(false);
      createForm.reset(DEFAULT_CREATE_VALUES);
      void queryClient.invalidateQueries({ queryKey: ["skillsets"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const usageSummary = useMemo(() => {
    const items = observations.data?.items ?? [];
    const totalTokens = items.reduce((acc, row) => acc + (row.total_tokens ?? 0), 0);
    const denials = items.filter((row) => row.budget_denied).length;
    const avgLatency = items.length
      ? Math.round(items.reduce((acc, row) => acc + (row.latency_ms ?? 0), 0) / Math.max(items.length, 1))
      : 0;
    return { totalTokens, denials, avgLatency, calls: items.length };
  }, [observations.data?.items]);

  const canWrite = role === "admin";
  const rows = query.data?.skillsets ?? [];

  const activeVersionByName = useMemo(() => {
    const map = new Map<string, SkillsetRead>();
    for (const row of rows) {
      if (row.is_active) map.set(row.name, row);
    }
    return map;
  }, [rows]);

  const createFormName = createForm.watch("name");
  const createFormVersion = createForm.watch("version");
  const createFormActivate = createForm.watch("activate");
  const activeForCreateName = activeVersionByName.get(createFormName.trim());

  const columns: ColumnDef<SkillsetRead, unknown>[] = [
    {
      id: "name",
      header: "Name",
      cell: ({ row }) => <code className="text-xs">{row.original.name}</code>
    },
    { id: "version", header: "Version", cell: ({ row }) => row.original.version },
    {
      id: "active",
      header: "Status",
      cell: ({ row }) =>
        row.original.is_active ? <Badge variant="success">Active</Badge> : <Badge variant="secondary">Inactive</Badge>
    },
    {
      id: "acceptance",
      header: "Acceptance rate",
      cell: ({ row }) => {
        const v = row.original.metrics?.acceptance_rate;
        return typeof v === "number" ? `${(v * 100).toFixed(0)}%` : "—";
      }
    },
    {
      id: "confidence",
      header: "Confidence",
      cell: ({ row }) => {
        const v = row.original.metrics?.confidence;
        return typeof v === "number" ? `${(v * 100).toFixed(0)}%` : "—";
      }
    },
    {
      id: "score",
      header: "Avg score",
      cell: ({ row }) => {
        const v = row.original.metrics?.avg_score;
        return typeof v === "number" ? v.toFixed(2) : "—";
      }
    },
    {
      id: "outcomes",
      header: "Outcomes",
      cell: ({ row }) => {
        const metrics = row.original.metrics;
        const v = metrics?.outcomes_count ?? metrics?.sample_count;
        return typeof v === "number" ? v : "—";
      }
    },
    {
      id: "created_at",
      header: "Created",
      cell: ({ row }) => (row.original.created_at ? new Date(row.original.created_at).toLocaleString() : "—")
    },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row }) => {
        const target = row.original;
        const currentlyActive = activeVersionByName.get(target.name);
        return (
          <div className="flex flex-wrap gap-1">
            <ConfirmAction
              title={`Activate ${target.name}@${target.version}?`}
              description={
                <LifecycleChangeSummary action="activate" target={target} currentlyActive={currentlyActive} />
              }
              onConfirm={() => activateMutation.mutate(target)}
              okText="Activate"
              disabled={!canWrite || target.is_active}
            >
              <Button size="sm" variant="secondary" disabled={!canWrite || target.is_active} loading={activateMutation.isPending}>
                Activate
              </Button>
            </ConfirmAction>
            <ConfirmAction
              title={`Roll back ${target.name} to ${target.version}?`}
              description={
                <LifecycleChangeSummary action="rollback" target={target} currentlyActive={currentlyActive} />
              }
              onConfirm={() => rollbackMutation.mutate(target)}
              okText="Roll back"
              disabled={!canWrite || target.is_active}
            >
              <Button size="sm" variant="secondary" disabled={!canWrite || target.is_active} loading={rollbackMutation.isPending}>
                Rollback
              </Button>
            </ConfirmAction>
            <Button
              size="sm"
              variant="secondary"
              disabled={!canWrite}
              onClick={() => {
                setSource(target);
                setPromoteOpen(true);
                promoteForm.reset({ target_version: "" });
              }}
            >
              Promote
            </Button>
          </div>
        );
      }
    }
  ];

  if (query.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (query.isError) {
    return (
      <Card>
        <CardContent className="pt-6 text-destructive">
          Failed to load skillsets: {(query.error as Error).message}
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="AI Skillsets"
        subtitle="Versioned prompt + tool definitions used by the Table Detail AI insights. Outcomes are recorded per recommendation; acceptance rate drives A/B rollouts. Only one version per skillset name is active at a time."
        actions={
          <Button
            variant="primary"
            disabled={!canWrite}
            onClick={() => {
              createForm.reset(DEFAULT_CREATE_VALUES);
              setCreateOpen(true);
            }}
          >
            Create candidate
          </Button>
        }
      />

      <Card>
        <CardContent className="flex flex-wrap gap-5 pt-6 text-sm text-muted-foreground">
          <span>
            LLM calls: <span className="text-foreground">{usageSummary.calls.toLocaleString()}</span>
          </span>
          <span>
            Tokens: <span className="text-foreground">{usageSummary.totalTokens.toLocaleString()}</span>
          </span>
          <span>
            Avg latency: <span className="text-foreground">{usageSummary.avgLatency} ms</span>
          </span>
          <span className={usageSummary.denials > 0 ? "text-[var(--color-status-warning)]" : undefined}>
            Budget denials: <span className="text-foreground">{usageSummary.denials}</span>
          </span>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-0 pt-0">
          {rows.length === 0 ? (
            <div className="p-4">
              <EmptyState
                title="No skillsets registered"
                description="The built-in iceberg_advisor skillset will seed on first use."
              />
            </div>
          ) : (
            <DataTable columns={columns} data={rows} getRowId={(row) => row.id} />
          )}
        </CardContent>
      </Card>

      <Dialog open={promoteOpen} onOpenChange={setPromoteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Promote candidate version</DialogTitle>
            <DialogDescription>
              Source candidate: <code>{source?.name}@{source?.version}</code>. Promoting clones it as a new version
              and activates it immediately.
            </DialogDescription>
          </DialogHeader>
          <FormProvider {...promoteForm}>
            <form
              className="space-y-4"
              onSubmit={promoteForm.handleSubmit((values) => {
                if (!source) return;
                promoteMutation.mutate({
                  name: source.name,
                  sourceVersion: source.version,
                  targetVersion: values.target_version.trim()
                });
              })}
            >
              <FormErrorSummary<z.infer<typeof promoteSchema>>
                labels={{ target_version: "Target promoted version" }}
              />
              <FormField name="target_version" label="Target promoted version">
                {(field) => <Input {...field} placeholder="1.2.0" />}
              </FormField>
              <DialogFooter>
                <Button type="button" variant="secondary" onClick={() => setPromoteOpen(false)}>
                  Cancel
                </Button>
                <Button type="submit" variant="primary" loading={promoteMutation.isPending}>
                  Promote
                </Button>
              </DialogFooter>
            </form>
          </FormProvider>
        </DialogContent>
      </Dialog>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Create skillset candidate</DialogTitle>
            <DialogDescription>
              Registers a new, inactive candidate version. Use Activate or Promote from the table once it&apos;s ready.
            </DialogDescription>
          </DialogHeader>
          <FormProvider {...createForm}>
            <form
              className="space-y-4"
              onSubmit={(e) => {
                // Activation is confirmed separately below (via the Create & activate button), so a
                // plain form submit (e.g. pressing Enter) never silently activates.
                if (createFormActivate) {
                  e.preventDefault();
                  toast.message('Click "Create & activate" to confirm activating this candidate.');
                  return;
                }
                void createForm.handleSubmit((values) => createMutation.mutate(values))(e);
              }}
            >
              <FormErrorSummary<CreateFormValues>
                labels={{ name: "Skillset name", version: "Version", prompts: "Prompts", tools: "Tools" }}
              />

              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Identity</p>
              <FormField name="name" label="Skillset name">
                {(field) => <Input {...field} placeholder="iceberg_advisor" />}
              </FormField>
              <FormField name="version" label="Version">
                {(field) => <Input {...field} placeholder="1.2.0" />}
              </FormField>

              <Separator />
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Prompts</p>
              <FormField name="prompts" label="Prompt templates">
                {(field, meta) => (
                  <PromptsEditor
                    value={field.value as string}
                    onChange={field.onChange}
                    error={meta.invalid ? createForm.formState.errors.prompts?.message : undefined}
                  />
                )}
              </FormField>
              <PromptPreviewHarness promptsJson={createForm.watch("prompts")} />

              <Separator />
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Tools (advanced)</p>
              <FormField
                name="tools"
                label="Tools (JSON array of { name, description, parameters })"
                description="Kept as JSON — each tool's shape varies too much for a generic structured form."
              >
                {(field) => <Textarea {...field} rows={4} className="mono-textarea font-mono text-xs" spellCheck={false} />}
              </FormField>

              <Separator />
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Review</p>
              <div className="flex items-center gap-2">
                <Checkbox
                  id="activate-on-create"
                  checked={createFormActivate}
                  onCheckedChange={(checked) => createForm.setValue("activate", checked === true)}
                />
                <Label htmlFor="activate-on-create">Activate immediately after create</Label>
              </div>
              {createFormActivate ? (
                <p className="text-xs text-muted-foreground">
                  {activeForCreateName
                    ? `This will replace ${activeForCreateName.name}@${activeForCreateName.version} as the active version.`
                    : "No version of this name is currently active — this candidate becomes the first active version."}
                </p>
              ) : null}

              <DialogFooter>
                <Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>
                  Cancel
                </Button>
                {createFormActivate ? (
                  <ConfirmAction
                    title="Create and activate immediately?"
                    description={
                      <div className="space-y-2 text-sm">
                        <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
                          <dt className="text-muted-foreground">New candidate</dt>
                          <dd className="font-medium">
                            {createFormName.trim() || "—"}@{createFormVersion.trim() || "—"}
                          </dd>
                          <dt className="text-muted-foreground">Currently active version</dt>
                          <dd className="font-medium">
                            {activeForCreateName ? activeForCreateName.version : "None currently active"}
                          </dd>
                        </dl>
                        <p className="text-muted-foreground">
                          {activeForCreateName
                            ? `Version ${activeForCreateName.version} will be marked inactive as soon as the new candidate is created.`
                            : "This candidate becomes the active version as soon as it's created."}{" "}
                          You can undo this afterward by activating any other version — nothing is deleted.
                        </p>
                      </div>
                    }
                    onConfirm={() => createForm.handleSubmit((values) => createMutation.mutate(values))()}
                    okText="Create & activate"
                  >
                    <Button type="button" variant="primary" loading={createMutation.isPending}>
                      Create
                    </Button>
                  </ConfirmAction>
                ) : (
                  <Button type="submit" variant="primary" loading={createMutation.isPending}>
                    Create
                  </Button>
                )}
              </DialogFooter>
            </form>
          </FormProvider>
        </DialogContent>
      </Dialog>
    </div>
  );
};
