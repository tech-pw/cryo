import type { AssistantStep } from "../../hooks/useAssistantStream";
import { formatDuration } from "../../lib/format";

export const WorkTrace = ({ steps }: { steps: AssistantStep[] }) => {
  if (steps.length === 0) return null;
  const working = steps.some((step) => step.status === "started");
  return (
    <details className="text-xs text-muted-foreground">
      <summary className="cursor-pointer font-medium text-foreground">{working ? "Working" : "Work completed"}</summary>
      <ol className="mt-2 space-y-1 pl-4">
        {steps.map((step) => (
          <li key={step.step}>
            <span>{step.label ?? `Step ${step.step}`}</span>
            {step.toolName ? <span className="ml-2 font-mono">{step.toolName}</span> : null}
            {step.status === "failed" ? <span> failed</span> : null}
            {typeof step.durationMs === "number" ? <span> · {formatDuration(step.durationMs)}</span> : null}
            {typeof step.rowCount === "number" ? <span> · {step.rowCount} rows</span> : null}
            {step.error ? <span> · {step.error}</span> : null}
          </li>
        ))}
      </ol>
    </details>
  );
};
