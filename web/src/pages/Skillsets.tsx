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
import { Input } from "../components/ui/input";
import { PageHeader } from "../components/ui/page-header";
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
  prompts: '{\n  "system": ""\n}',
  tools: "[]",
  activate: false
};

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
        row.original.is_active ? <Badge variant="success">active</Badge> : <Badge variant="secondary">inactive</Badge>
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
      header: "Lifecycle",
      cell: ({ row }) => (
        <div className="flex flex-wrap gap-1">
          <Button
            size="sm"
            variant="secondary"
            disabled={!canWrite || row.original.is_active}
            loading={activateMutation.isPending}
            onClick={() => activateMutation.mutate(row.original)}
          >
            Activate
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={!canWrite || row.original.is_active}
            loading={rollbackMutation.isPending}
            onClick={() => rollbackMutation.mutate(row.original)}
          >
            Rollback
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={!canWrite}
            onClick={() => {
              setSource(row.original);
              setPromoteOpen(true);
              promoteForm.reset({ target_version: "" });
            }}
          >
            Promote
          </Button>
        </div>
      )
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
        subtitle="Versioned prompt + tool definitions used by the Table Detail AI insights. Outcomes are recorded per recommendation; acceptance rate drives A/B rollouts."
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
              Source candidate: <code>{source?.name}@{source?.version}</code>
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
              onSubmit={createForm.handleSubmit((values) => createMutation.mutate(values))}
            >
              <FormField name="name" label="Skillset name">
                {(field) => <Input {...field} placeholder="iceberg_advisor" />}
              </FormField>
              <FormField name="version" label="Version">
                {(field) => <Input {...field} placeholder="1.2.0" />}
              </FormField>
              <FormField name="prompts" label="Prompts (JSON object of name -> template)">
                {(field) => <Textarea {...field} rows={6} className="mono-textarea font-mono text-xs" spellCheck={false} />}
              </FormField>
              <FormField name="tools" label="Tools (JSON array)">
                {(field) => <Textarea {...field} rows={4} className="mono-textarea font-mono text-xs" spellCheck={false} />}
              </FormField>
              <div className="flex items-center gap-2">
                <Checkbox
                  id="activate-on-create"
                  checked={createForm.watch("activate")}
                  onCheckedChange={(checked) => createForm.setValue("activate", checked === true)}
                />
                <Label htmlFor="activate-on-create">Activate immediately after create</Label>
              </div>
              <DialogFooter>
                <Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>
                  Cancel
                </Button>
                <Button type="submit" variant="primary" loading={createMutation.isPending}>
                  Create
                </Button>
              </DialogFooter>
            </form>
          </FormProvider>
        </DialogContent>
      </Dialog>
    </div>
  );
};
