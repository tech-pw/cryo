import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { sendAssistantFeedback } from "../../api/assistant";
import { useAuth } from "../../lib/auth";
import { Button } from "../ui/button";
import { Textarea } from "../ui/textarea";

export const FeedbackControls = ({ messageId }: { messageId: string | null }) => {
  const { api } = useAuth();
  const [rating, setRating] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const feedback = useMutation({
    mutationFn: (next: { rating: number; reason?: string }) => {
      if (!messageId) throw new Error("Missing message");
      return sendAssistantFeedback(api, messageId, next);
    },
    onSuccess: (_data, variables) => setRating(variables.rating)
  });

  if (!messageId) return null;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-1">
        <Button
          type="button"
          size="icon"
          variant={rating === 1 ? "secondary" : "ghost"}
          aria-label="Helpful"
          aria-pressed={rating === 1}
          loading={feedback.isPending && feedback.variables?.rating === 1}
          onClick={() => feedback.mutate({ rating: 1 })}
        >
          <ThumbsUp />
        </Button>
        <Button
          type="button"
          size="icon"
          variant={rating === -1 ? "secondary" : "ghost"}
          aria-label="Not helpful"
          aria-pressed={rating === -1}
          onClick={() => setRating(-1)}
        >
          <ThumbsDown />
        </Button>
      </div>
      {rating === -1 ? (
        <form
          className="space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            feedback.mutate({ rating: -1, reason: reason.trim() || undefined });
          }}
        >
          <label className="sr-only" htmlFor={`feedback-${messageId}`}>
            What was wrong
          </label>
          <Textarea
            id={`feedback-${messageId}`}
            rows={2}
            value={reason}
            placeholder="What was wrong?"
            onChange={(event) => setReason(event.target.value)}
          />
          <Button type="submit" size="sm" variant="secondary" loading={feedback.isPending}>
            Send feedback
          </Button>
        </form>
      ) : null}
    </div>
  );
};
