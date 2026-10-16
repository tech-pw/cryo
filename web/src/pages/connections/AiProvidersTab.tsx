import { Bot, Plus, Trash2 } from "lucide-react";
import { ConfirmAction } from "../../components/confirm";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { ConnectionList, ConnectionRow, ConnectionSection, ConnectionStatus } from "./ConnectionList";
import { useConnectionsContext } from "./ConnectionsContext";

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  azure_openai: "Azure OpenAI"
};

export const AiProvidersTab = () => {
  const { aiProviders, setAiProviderDrawer, testAiProviderMutation, deleteAiProviderMutation } =
    useConnectionsContext();

  const newButton = (
    <Button variant="primary" onClick={() => setAiProviderDrawer("new")}>
      <Plus aria-hidden="true" />
      New AI provider
    </Button>
  );

  return (
    <ConnectionSection
      title="AI providers"
      description="Anthropic, OpenAI, or Azure OpenAI keys power Ask Cryo, health explanations, and recommendations. Keys are encrypted at rest and never logged."
      action={newButton}
    >
      <ConnectionList
        label="AI providers"
        loading={aiProviders.isLoading}
        emptyTitle="No AI providers configured"
        emptyDescription="Add an Anthropic or OpenAI API key to turn on Ask Cryo and AI health explanations."
      >
        {(aiProviders.data ?? []).map((provider) => (
          <ConnectionRow
            key={provider.id ?? `ai-${provider.provider_type}-${provider.name}`}
            icon={<Bot />}
            name={provider.name}
            kind={PROVIDER_LABELS[provider.provider_type] ?? provider.provider_type}
            enabled={Boolean(provider.enabled)}
            status={<ConnectionStatus enabled={Boolean(provider.enabled)} />}
            badges={provider.is_default ? <Badge variant="default">Default</Badge> : null}
            details={[
              { label: "Model", value: String(provider.model), mono: true },
              { label: "API key", value: provider.has_api_key ? "Configured" : "Missing" }
            ]}
            actions={
              <>
                <Button size="sm" variant="secondary" onClick={() => setAiProviderDrawer(provider)}>
                  Edit
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  loading={testAiProviderMutation.isPending}
                  disabled={!provider.has_api_key}
                  onClick={() => testAiProviderMutation.mutate(provider.id)}
                >
                  Test
                </Button>
                <ConfirmAction
                  title="Delete this AI provider?"
                  description="Copilot and other AI features will stop using this key immediately."
                  onConfirm={() => deleteAiProviderMutation.mutate(provider.id)}
                >
                  <Button
                    size="sm"
                    variant="ghost"
                    className="connection-delete px-2"
                    aria-label="Delete"
                    title={`Delete ${provider.name}`}
                    loading={deleteAiProviderMutation.isPending}
                  >
                    {deleteAiProviderMutation.isPending ? null : <Trash2 aria-hidden="true" />}
                  </Button>
                </ConfirmAction>
              </>
            }
          />
        ))}
      </ConnectionList>
    </ConnectionSection>
  );
};

export const AiProvidersTabLabel = () => (
  <span className="inline-flex items-center gap-1.5">
    <Bot className="size-3.5" aria-hidden="true" />
    AI Providers
  </span>
);
