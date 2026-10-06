import { ChevronDown } from "lucide-react";
import { type ReactNode, useState } from "react";
import { cn } from "../../lib/cn";

export interface CollapsibleProps {
  label: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
}

export function Collapsible({ label, children, defaultOpen = false, className }: CollapsibleProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={className}>
      <button
        type="button"
        className="flex w-full items-center gap-2 py-2 text-left text-sm font-medium text-foreground"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} />
        {label}
      </button>
      {open ? <div className="pb-2">{children}</div> : null}
    </div>
  );
}
