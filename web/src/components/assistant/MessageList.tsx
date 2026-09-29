import type { AssistantBlock } from "../../api/assistant";
import type { TranscriptMessage } from "../../hooks/useAssistantStream";
import { ActionReviewCard } from "./actions/ActionReviewCard";
import { ExecutionCard } from "./actions/ExecutionCard";
import { OutcomeCard } from "./actions/OutcomeCard";
import { AnswerBlock } from "./blocks/AnswerBlock";
import { ChartBlock } from "./blocks/ChartBlock";
import { ComparisonBlock } from "./blocks/ComparisonBlock";
import { GapBlock } from "./blocks/GapBlock";
import { InvestigationBlock } from "./blocks/InvestigationBlock";
import { RankedTableBlock } from "./blocks/RankedTableBlock";
import { VerifiedBadge } from "./blocks/VerifiedBadge";
import { FeedbackControls } from "./FeedbackControls";
import { WorkTrace } from "./WorkTrace";

const rank = (type: AssistantBlock["type"]): number => {
  if (type === "verified" || type === "answer") return 0;
  if (type === "ranked_table" || type === "comparison" || type === "chart" || type === "investigation") return 1;
  if (type === "gap") return 2;
  return 3;
};

const orderBlocks = (blocks: AssistantBlock[]): AssistantBlock[] =>
  blocks
    .map((block, index) => ({ block, index }))
    .sort((left, right) => rank(left.block.type) - rank(right.block.type) || left.index - right.index)
    .map((item) => item.block);

const BlockView = ({ block }: { block: AssistantBlock }) => {
  switch (block.type) {
    case "answer":
      return <AnswerBlock block={block} />;
    case "ranked_table":
      return <RankedTableBlock block={block} />;
    case "comparison":
      return <ComparisonBlock block={block} />;
    case "chart":
      return <ChartBlock block={block} />;
    case "investigation":
      return <InvestigationBlock block={block} />;
    case "gap":
      return <GapBlock block={block} />;
    case "verified":
      return (
        <div className="space-y-2">
          <VerifiedBadge handler={block.handler} />
          {block.text ? <p className="m-0 whitespace-pre-wrap text-sm">{block.text}</p> : null}
        </div>
      );
    case "action_review":
      return <ActionReviewCard proposalId={block.proposal_id} block={block} />;
    case "execution":
      return <ExecutionCard proposalId={block.proposal_id} />;
    case "outcome":
      return <OutcomeCard block={block} />;
    default:
      return <p className="text-sm text-muted-foreground">-</p>;
  }
};

const announcement = (message: TranscriptMessage): string => {
  if (message.text.trim()) return message.text;
  const answer = message.blocks.find((block) => block.type === "answer");
  if (answer && answer.type === "answer") return answer.text;
  return "Answer ready";
};

export const MessageList = ({ messages }: { messages: TranscriptMessage[] }) => (
  <div className="space-y-4">
    <div className="sr-only" aria-live="polite" aria-atomic="true">
      {messages
        .filter((message) => message.announce)
        .map((message) => announcement(message))
        .join(" ")}
    </div>
    {messages.map((message) => {
      const blocks = orderBlocks(message.blocks);
      const answer = blocks.filter((block) => rank(block.type) === 0);
      const evidence = blocks.filter((block) => rank(block.type) === 1);
      const gaps = blocks.filter((block) => rank(block.type) === 2);
      const next = blocks.filter((block) => rank(block.type) === 3);
      return (
        <article key={message.id} className="space-y-3">
          <p className="text-xs font-medium text-muted-foreground">{message.role === "user" ? "You" : "Cryo"}</p>
          {message.role === "user" ? <p className="m-0 whitespace-pre-wrap text-sm">{message.text}</p> : null}
          {message.role === "assistant" && message.text && answer.length === 0 ? (
            <p className="m-0 whitespace-pre-wrap text-sm">{message.text}</p>
          ) : null}
          {message.clarification ? <p className="text-sm text-muted-foreground">{message.clarification}</p> : null}
          {answer.map((block, index) => (
            <BlockView key={`${message.id}-answer-${index}`} block={block} />
          ))}
          {evidence.length > 0 ? (
            <section className="space-y-2">
              <h3 className="text-xs font-medium text-muted-foreground">Evidence</h3>
              {evidence.map((block, index) => (
                <BlockView key={`${message.id}-evidence-${index}`} block={block} />
              ))}
            </section>
          ) : null}
          {gaps.map((block, index) => (
            <BlockView key={`${message.id}-gap-${index}`} block={block} />
          ))}
          {next.length > 0 ? (
            <section className="space-y-2">
              <h3 className="text-xs font-medium text-muted-foreground">Next step</h3>
              {next.map((block, index) => (
                <BlockView key={`${message.id}-next-${index}`} block={block} />
              ))}
            </section>
          ) : null}
          {message.error ? <p className="text-sm text-destructive">{message.error}</p> : null}
          {message.role === "assistant" ? <WorkTrace steps={message.steps} /> : null}
          {message.role === "assistant" && message.status === "completed" ? (
            <FeedbackControls messageId={message.serverId} />
          ) : null}
        </article>
      );
    })}
  </div>
);
