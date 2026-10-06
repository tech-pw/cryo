import type { RefObject } from "react";
import type { AssistantCapabilities, AssistantStarter } from "../../api/assistant";
import type { TranscriptMessage } from "../../hooks/useAssistantStream";
import { Composer } from "./Composer";
import { ContextChips } from "./ContextChips";
import { MessageList } from "./MessageList";
import { StarterQuestions } from "./StarterQuestions";

export const ConversationView = ({
  messages,
  starters,
  capabilities,
  context,
  draft,
  streaming,
  composerRef,
  onDraft,
  onSend,
  onStop,
  onStarter,
  onRemoveContext
}: {
  messages: TranscriptMessage[];
  starters: AssistantStarter[];
  capabilities?: AssistantCapabilities;
  context: Record<string, unknown>;
  draft: string;
  streaming: boolean;
  composerRef: RefObject<HTMLTextAreaElement | null>;
  onDraft: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onStarter: (starter: AssistantStarter) => void;
  onRemoveContext: (key: string) => void;
}) => {
  const enabled = capabilities?.enabled !== false;
  const freeform = capabilities ? capabilities.provider_configured : true;
  const knownOff = capabilities?.enabled === false;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-3">
        <ContextChips context={context} onRemove={onRemoveContext} />
        {messages.length === 0 ? (
          <div className="space-y-3">
            {knownOff ? (
              <p className="text-sm text-muted-foreground">Ask Cryo is turned off. An admin can enable it in AI model settings.</p>
            ) : (
              <p className="text-sm text-muted-foreground">
                {capabilities && !freeform
                  ? "Free-form questions need a configured AI provider. Verified starters still work."
                  : "Ask about a table, a run, or the fleet."}
              </p>
            )}
            <StarterQuestions starters={starters} disabled={!enabled} onSelect={onStarter} />
          </div>
        ) : (
          <MessageList messages={messages} />
        )}
      </div>
      <Composer
        ref={composerRef}
        value={draft}
        onChange={onDraft}
        onSend={onSend}
        onStop={onStop}
        streaming={streaming}
        disabled={!enabled || !freeform}
        placeholder={freeform ? "Ask about this table, run, or the fleet" : "Free-form needs a provider"}
      />
    </div>
  );
};
