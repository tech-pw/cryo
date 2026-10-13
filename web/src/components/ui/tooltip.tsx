import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { forwardRef, type ComponentPropsWithoutRef, type ReactElement, type ReactNode } from "react";
import { cn } from "../../lib/cn";

const TooltipProvider = TooltipPrimitive.Provider;

const Tooltip = TooltipPrimitive.Root;

const TooltipTrigger = TooltipPrimitive.Trigger;

const TooltipContent = forwardRef<
  React.ElementRef<typeof TooltipPrimitive.Content>,
  ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 4, ...props }, ref) => (
  <TooltipPrimitive.Portal>
    <TooltipPrimitive.Content
      ref={ref}
      sideOffset={sideOffset}
      className={cn(
        "z-50 overflow-hidden rounded-[var(--radius-sm)] border border-border bg-popover px-3 py-1.5 text-xs text-popover-foreground shadow-sm",
        "animate-in fade-in-0 zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
        "data-[side=bottom]:slide-in-from-top-1 data-[side=left]:slide-in-from-right-1 data-[side=right]:slide-in-from-left-1 data-[side=top]:slide-in-from-bottom-1",
        "duration-[var(--motion-fast)] ease-[var(--ease-out)]",
        className
      )}
      {...props}
    />
  </TooltipPrimitive.Portal>
));
TooltipContent.displayName = TooltipPrimitive.Content.displayName;

function CryoTooltipProvider({ children }: { children: React.ReactNode }) {
  return (
    <TooltipProvider delayDuration={400} skipDelayDuration={0}>
      {children}
    </TooltipProvider>
  );
}

function TruncationTooltip({
  content,
  children,
  contentClassName
}: {
  content?: ReactNode;
  children: ReactElement;
  contentClassName?: string;
}) {
  if (content == null || content === "") return children;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent className={contentClassName}>{content}</TooltipContent>
    </Tooltip>
  );
}

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider, CryoTooltipProvider, TruncationTooltip };
