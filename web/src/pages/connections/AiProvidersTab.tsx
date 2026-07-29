import type { ColumnDef } from "@tanstack/react-table";
import { Bot, CheckCircle2, Plus, XCircle } from "lucide-react";
import { useMemo } from "react";
import type { AIProviderRead } from "../../api/client";
import { ConfirmAction } from "../../components/confirm";
import { StatusTag } from "../../components/status";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { DataTable } from "../../components/ui/data-table";
import { useConnectionsContext } from "./ConnectionsContext";

export const AiProvidersTab = () => {
  const {
    aiProviders,
    setAiProviderDrawer,
    testAiProviderMutation,
    deleteAiProviderMutation
  } = useConnectionsContext();

  const columns = useMemo<ColumnDef<AIProviderRead, unknown>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Name",
        cell: ({ row }) => (
          <div className="flex items-center gap-2">
            <span className="font-medium">{row.original.name}</span>
            {row.original.is_default ? <Badge variant="default">Default</Badge> : null}
          </div>
        )
      },
      {
        accessorKey: "provider_type",
        header: "Provider",
        cell: ({ getValue }) => {
          const value = String(getValue());
          return (
            <Badge variant={value === "anthropic" ? "warning" : "success"}>
              {value === "anthropic" ? "Anthropic" : "OpenAI"}
            </Badge>
          );
        }
      },
      {
        accessorKey: "model",
        header: "Model",
        cell: ({ getValue }) => <code className="text-xs">{String(getValue())}</code>
      },
      {
        accessorKey: "has_api_key",
        header: "API Key",
        cell: ({ getValue }) =>
          getValue() ? (
            <span className="inline-flex items-center gap-1.5 text-muted-foreground">
              <CheckCircle2 className="size-3.5 text-[var(--color-status-success)]" aria-hidden="true" />
              Configured
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 text-muted-foreground">
              <XCircle className="size-3.5 text-destructive" aria-hidden="true" />
              Missing
            </span>
          )
      },
      {
        accessorKey: "enabled",
        header: "Enabled",
        cell: ({ getValue }) => <StatusTag status={getValue() ? "succeeded" : "skipped"} />
      },
      {
        id: "actions",
        header: "",
        size: 220,
        cell: ({ row }) => (
          <div className="flex flex-wrap items-center gap-1">
            <Button size="sm" variant="secondary" onClick={() => setAiProviderDrawer(row.original)}>
              Edit
            </Button>
            <Button
              size="sm"
              variant="secondary"
              loading={testAiProviderMutation.isPending}
              disabled={!row.original.has_api_key}
              onClick={() => testAiProviderMutation.mutate(row.original.id)}
            >
              Test
            </Button>
            <ConfirmAction
              title="Delete this AI provider?"
              description="Copilot and other AI features will stop using this key immediately."
              onConfirm={() => deleteAiProviderMutation.mutate(row.original.id)}
            >
              <Button size="sm" variant="danger" loading={deleteAiProviderMutation.isPending}>
                Delete
              </Button>
            </ConfirmAction>
          </div>
        )
      }
    ],
    [deleteAiProviderMutation, setAiProviderDrawer, testAiProviderMutation]
  );

  return (
    <div className="flex w-full flex-col gap-3">
      <p className="tab-intro text-sm text-muted-foreground">
        Connect an AI provider (Anthropic Claude or OpenAI GPT) to enable natural language queries,
        intelligent health explanations, and AI-powered recommendations. API keys are stored encrypted and
        never logged.
      </p>
      <div>
        <Button variant="primary" onClick={() => setAiProviderDrawer("new")}>
          <Plus className="size-4" aria-hidden="true" />
          New AI provider
        </Button>
      </div>
      <DataTable
        columns={columns}
        data={aiProviders.data ?? []}
        density="compact"
        loading={aiProviders.isLoading}
        getRowId={(row) => row.id ?? `ai-${row.provider_type}-${row.name}`}
        emptyTitle="No AI providers configured"
        emptyDescription="Add an Anthropic or OpenAI API key to enable AI features like natural language queries and intelligent recommendations."
      />
    </div>
  );
};

export const AiProvidersTabLabel = () => (
  <span className="inline-flex items-center gap-1.5">
    <Bot className="size-3.5" aria-hidden="true" />
    AI Providers
  </span>
);
