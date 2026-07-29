import * as SwitchPrimitive from "@radix-ui/react-switch";
import { forwardRef, type ComponentPropsWithoutRef } from "react";
import { cn } from "../../lib/cn";

type SwitchProps = ComponentPropsWithoutRef<typeof SwitchPrimitive.Root> & {
  /** Ant Design Form compatibility when `valuePropName="checked"`. */
  onChange?: (checked: boolean) => void;
};

const Switch = forwardRef<React.ElementRef<typeof SwitchPrimitive.Root>, SwitchProps>(
  ({ className, onChange, onCheckedChange, ...props }, ref) => (
  <SwitchPrimitive.Root
    className={cn(
      "peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors duration-[var(--motion-fast)] ease-[var(--ease-standard)]",
      "focus-visible:outline-none focus-visible:ring-[var(--focus-ring-width)] focus-visible:ring-ring focus-visible:ring-offset-[var(--focus-ring-offset)]",
      "disabled:cursor-not-allowed disabled:opacity-60",
      "data-[state=checked]:bg-primary data-[state=unchecked]:bg-[var(--color-border-strong)]",
      className
    )}
    onCheckedChange={(checked) => {
      onCheckedChange?.(checked);
      onChange?.(checked);
    }}
    {...props}
    ref={ref}
  >
    <SwitchPrimitive.Thumb
      className={cn(
        "pointer-events-none block h-4 w-4 rounded-full bg-background shadow-sm ring-0 transition-transform duration-[var(--motion-fast)] ease-[var(--ease-standard)]",
        "data-[state=checked]:translate-x-4 data-[state=unchecked]:translate-x-0"
      )}
    />
  </SwitchPrimitive.Root>
  )
);
Switch.displayName = SwitchPrimitive.Root.displayName;

export { Switch };
