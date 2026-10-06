import { History, Maximize2, Minimize2, Plus } from "lucide-react";
import { Button } from "../ui/button";
import { SheetDescription, SheetTitle } from "../ui/sheet";

export const DrawerHeader = ({
  expanded,
  historyOpen,
  onExpand,
  onHistory,
  onNew
}: {
  expanded: boolean;
  historyOpen: boolean;
  onExpand: () => void;
  onHistory: () => void;
  onNew: () => void;
}) => (
  <div className="flex items-start justify-between gap-2 border-b border-border px-4 py-3 pr-12">
    <div className="min-w-0">
      <SheetTitle className="text-sm">Ask Cryo</SheetTitle>
      <SheetDescription className="text-xs">Answer first, then the evidence.</SheetDescription>
    </div>
    <div className="flex shrink-0 items-center gap-1">
      <Button type="button" size="icon" variant="ghost" aria-label="New conversation" onClick={onNew}>
        <Plus />
      </Button>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        aria-pressed={historyOpen}
        aria-label="Conversation history"
        onClick={onHistory}
      >
        <History />
      </Button>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        aria-pressed={expanded}
        aria-label={expanded ? "Restore Ask Cryo width" : "Expand Ask Cryo"}
        onClick={onExpand}
      >
        {expanded ? <Minimize2 /> : <Maximize2 />}
      </Button>
    </div>
  </div>
);
