import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  assistantErrorMessage,
  deleteAssistantConversation,
  listAssistantConversations,
  shareAssistantConversation,
  unshareAssistantConversation
} from "../../api/assistant";
import { useAuth } from "../../lib/auth";
import { formatTime } from "../../lib/format";
import { ConfirmAction } from "../confirm";
import { Alert } from "../ui/alert";
import { Button } from "../ui/button";

export const ConversationHistory = ({
  activeId,
  onSelect
}: {
  activeId: string | null;
  onSelect: (id: string) => void;
}) => {
  const { api, token } = useAuth();
  const queryClient = useQueryClient();
  const conversations = useQuery({
    queryKey: ["assistant", "conversations", token],
    queryFn: () => listAssistantConversations(api, { limit: 50 }),
    retry: false
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteAssistantConversation(api, id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["assistant", "conversations"] })
  });
  const share = useMutation({
    mutationFn: (id: string) => shareAssistantConversation(api, id)
  });
  const unshare = useMutation({
    mutationFn: (id: string) => unshareAssistantConversation(api, id)
  });
  const shared = share.data?.share_url ?? share.data?.url ?? share.data?.token;

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-medium">History</h3>
      {conversations.isError ? <Alert variant="error" title="Could not load history" description={assistantErrorMessage(conversations.error)} /> : null}
      <ul className="space-y-2">
        {(conversations.data?.items ?? []).map((item) => (
          <li key={item.id} className="rounded-[var(--radius-default)] border border-border p-2">
            <button type="button" className="w-full text-left text-sm" onClick={() => onSelect(item.id)}>
              <span className="font-medium">{item.title?.trim() || "Untitled"}</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">{formatTime(item.updated_at)}</span>
              {item.id === activeId ? <span className="sr-only">Current</span> : null}
            </button>
            <div className="mt-2 flex flex-wrap gap-1">
              <Button type="button" size="sm" variant="ghost" onClick={() => share.mutate(item.id)}>
                Share
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => unshare.mutate(item.id)}>
                Stop sharing
              </Button>
              <ConfirmAction title="Delete this conversation?" onConfirm={() => remove.mutate(item.id)}>
                <Button type="button" size="sm" variant="ghost">
                  Delete
                </Button>
              </ConfirmAction>
            </div>
          </li>
        ))}
      </ul>
      {shared ? <p className="break-all text-xs text-muted-foreground">Share link {shared}</p> : null}
      {!conversations.isLoading && (conversations.data?.items.length ?? 0) === 0 ? (
        <p className="text-sm text-muted-foreground">No saved conversations.</p>
      ) : null}
    </div>
  );
};
