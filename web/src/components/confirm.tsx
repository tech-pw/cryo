import { cloneElement, useState, type MouseEvent, type ReactElement, type ReactNode } from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle
} from "./ui/alert-dialog";
import { cn } from "../lib/cn";

interface OkButtonProps {
  danger?: boolean;
  loading?: boolean;
  disabled?: boolean;
}

interface TriggerElementProps {
  onClick?: (e: MouseEvent<HTMLElement>) => void;
  disabled?: boolean;
}

export interface ConfirmActionProps {
  children: ReactElement<TriggerElementProps>;
  title: ReactNode;
  description?: ReactNode;
  onConfirm?: (e?: MouseEvent<HTMLElement>) => void | Promise<void>;
  onCancel?: (e?: MouseEvent<HTMLElement>) => void;
  onOpenChange?: (open: boolean) => void;
  okText?: string;
  cancelText?: string;
  okButtonProps?: OkButtonProps;
  disabled?: boolean;
}

/** Mandatory confirmation before destructive or fleet-wide actions (Radix AlertDialog). */
export const ConfirmAction = ({
  title,
  description,
  children,
  onConfirm,
  onCancel,
  onOpenChange,
  okText = "Confirm",
  cancelText = "Cancel",
  okButtonProps,
  disabled
}: ConfirmActionProps) => {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);

  const setOpenState = (next: boolean) => {
    if (pending && !next) return;
    setOpen(next);
    onOpenChange?.(next);
  };

  const trigger = cloneElement(children, {
    onClick: (e: MouseEvent<HTMLElement>) => {
      children.props.onClick?.(e);
      if (disabled) return;
      e.preventDefault();
      e.stopPropagation();
      setOpenState(true);
    },
    disabled: disabled ?? children.props.disabled
  });

  const handleConfirm = async (e: MouseEvent<HTMLButtonElement>) => {
    e.preventDefault();
    try {
      setPending(true);
      await onConfirm?.(e);
      setOpenState(false);
    } finally {
      setPending(false);
    }
  };

  const handleCancel = (e: MouseEvent<HTMLButtonElement>) => {
    onCancel?.(e);
    setOpenState(false);
  };

  return (
    <AlertDialog open={open} onOpenChange={setOpenState}>
      {trigger}
      <AlertDialogContent onClick={(e) => e.stopPropagation()}>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          {description ? <AlertDialogDescription>{description}</AlertDialogDescription> : null}
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={handleCancel} disabled={pending}>{cancelText}</AlertDialogCancel>
          <AlertDialogAction
            onClick={handleConfirm}
            disabled={pending || okButtonProps?.disabled}
            className={cn(
              okButtonProps?.danger &&
                "bg-destructive text-destructive-foreground hover:bg-destructive/90 focus-visible:ring-destructive"
            )}
          >
            {pending || okButtonProps?.loading ? "…" : okText}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
};
