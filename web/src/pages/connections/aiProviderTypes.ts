import type { AIProviderType } from "../../api/client";

export interface AIProviderFormValues {
  name: string;
  provider_type: string;
  api_key: string;
  model: string;
  enabled: boolean;
  is_default: boolean;
  config_api_base?: string;
  config_api_version?: string;
  [key: `config_${string}`]: string | undefined;
}

export const DEFAULT_PROVIDER_TYPES: AIProviderType[] = [
  {
    type: "anthropic",
    name: "Anthropic",
    models: ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-3-5-sonnet-20241022"],
    default_model: "claude-sonnet-4-20250514",
    config_fields: []
  },
  {
    type: "openai",
    name: "OpenAI",
    models: ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
    default_model: "gpt-4o",
    config_fields: []
  },
  {
    type: "azure_openai",
    name: "Azure OpenAI",
    models: ["gpt4o", "gpt-4", "gpt-4-turbo", "gpt-35-turbo"],
    default_model: "gpt4o",
    config_fields: [
      { name: "api_base", label: "API Base URL", placeholder: "https://your-resource.openai.azure.com", required: true },
      { name: "api_version", label: "API Version", placeholder: "2024-03-01-preview", required: false, default: "2024-03-01-preview" }
    ]
  }
];
