import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { Loader2 } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "../../lib/cn";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[var(--radius-default)] text-sm font-medium transition-colors duration-[var(--motion-fast)] ease-[var(--ease-standard)] focus-visible:outline-none focus-visible:ring-[var(--focus-ring-width)] focus-visible:ring-ring focus-visible:ring-offset-[var(--focus-ring-offset)] disabled:pointer-events-none disabled:opacity-60 disabled:cursor-not-allowed [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary:
          "bg-primary text-primary-foreground hover:bg-[var(--color-primary-hover)] active:bg-[var(--color-primary-hover)]",
        secondary:
          "border border-border bg-background text-foreground hover:bg-[var(--color-surface-sunken)] active:bg-[var(--color-panel)]",
        outline:
          "border border-border bg-background text-foreground hover:bg-[var(--color-surface-sunken)] active:bg-[var(--color-panel)]",
        ghost:
          "text-foreground hover:bg-[var(--color-surface-sunken)] active:bg-[var(--color-panel)]",
        danger:
          "bg-destructive text-destructive-foreground hover:opacity-90 active:opacity-95"
      },
      size: {
        sm: "h-[var(--control-height-sm)] px-3 text-xs",
        default: "h-[var(--control-height)] px-4 text-sm",
        lg: "h-[var(--control-height-lg)] px-5 text-sm",
        icon: "h-[var(--control-height)] w-[var(--control-height)] p-0"
      }
    },
    defaultVariants: {
      variant: "secondary",
      size: "default"
    }
  }
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  loading?: boolean;
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, loading = false, disabled, children, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        disabled={disabled || loading}
        aria-busy={loading || undefined}
        {...props}
      >
        {asChild ? (
          children
        ) : (
          <>
            {loading ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
            {children}
          </>
        )}
      </Comp>
    );
  }
);
Button.displayName = "Button";

export { Button, buttonVariants };
