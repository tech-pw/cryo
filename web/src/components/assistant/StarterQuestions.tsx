import type { AssistantStarter } from "../../api/assistant";
import { Button } from "../ui/button";

export const StarterQuestions = ({
  starters,
  disabled,
  onSelect
}: {
  starters: AssistantStarter[];
  disabled?: boolean;
  onSelect: (starter: AssistantStarter) => void;
}) => {
  if (starters.length === 0) return null;
  return (
    <div className="space-y-2">
      <p className="text-xs font-medium text-muted-foreground">Starters</p>
      <ul className="flex flex-col gap-1.5">
        {starters.map((starter) => (
          <li key={`${starter.verified_handler ?? "free"}-${starter.text}`}>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="h-auto w-full justify-start whitespace-normal px-3 py-2 text-left"
              disabled={disabled}
              onClick={() => onSelect(starter)}
            >
              {starter.text}
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
};
