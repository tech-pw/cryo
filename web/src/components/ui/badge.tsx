import { cva, type VariantProps } from "class-variance-authority";
import { type HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

const badgeVariants = cva(
  "inline-flex items-center rounded-[var(--radius-sm)] border px-2 py-0.5 text-xs font-medium transition-colors duration-[var(--motion-fast)]",
  {
    variants: {
      variant: {
        default: "border-transparent bg-[var(--color-primary-soft)] text-[var(--color-primary)]",
        secondary: "border-border bg-[var(--color-surface-sunken)] text-foreground",
        outline: "border-border bg-transparent text-foreground",
        success: "border-transparent bg-[color-mix(in_srgb,var(--color-status-success)_14%,transparent)] text-[var(--color-status-success)]",
        warning: "border-transparent bg-[color-mix(in_srgb,var(--color-status-warning)_14%,transparent)] text-[var(--color-status-warning)]",
        danger: "border-transparent bg-[color-mix(in_srgb,var(--color-status-danger)_14%,transparent)] text-[var(--color-status-danger)]",
        gradeA: "border-transparent bg-[color-mix(in_srgb,var(--grade-a)_14%,transparent)] text-[var(--grade-a)]",
        gradeB: "border-transparent bg-[color-mix(in_srgb,var(--grade-b)_14%,transparent)] text-[var(--grade-b)]",
        gradeC: "border-transparent bg-[color-mix(in_srgb,var(--grade-c)_14%,transparent)] text-[var(--grade-c)]",
        gradeD: "border-transparent bg-[color-mix(in_srgb,var(--grade-d)_14%,transparent)] text-[var(--grade-d)]",
        gradeF: "border-transparent bg-[color-mix(in_srgb,var(--grade-f)_14%,transparent)] text-[var(--grade-f)]"
      }
    },
    defaultVariants: {
      variant: "default"
    }
  }
);

export interface BadgeProps extends HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
