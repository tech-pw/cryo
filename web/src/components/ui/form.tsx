import { zodResolver } from "@hookform/resolvers/zod";
import {
  Controller,
  FormProvider,
  useForm,
  useFormContext,
  type ControllerRenderProps,
  type FieldPath,
  type FieldValues,
  type UseFormProps,
  type UseFormReturn
} from "react-hook-form";
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from "react";
import type { z } from "zod";

import { cn } from "../../lib/cn";
import { Label } from "./label";

export { FormProvider, useForm, useFormContext, zodResolver };
export type { UseFormProps, UseFormReturn };

/** Deterministic DOM id for a field name — shared by the `<Label htmlFor>`, the rendered
 * control, its error text, and `FormErrorSummary`'s deep links/focus targets. Keep this the
 * single source of truth rather than re-deriving the id at each call site. */
export const formFieldId = (name: string): string => `field-${name}`;
export const formFieldErrorId = (name: string): string => `${formFieldId(name)}-error`;
export const formFieldDescriptionId = (name: string): string => `${formFieldId(name)}-description`;

export interface FormFieldRenderMeta {
  id: string;
  invalid: boolean;
  describedBy?: string;
}

export interface FormFieldProps<
  TFieldValues extends FieldValues = FieldValues,
  TName extends FieldPath<TFieldValues> = FieldPath<TFieldValues>
> {
  name: TName;
  label: ReactNode;
  className?: string;
  description?: string;
  children: (
    field: ControllerRenderProps<TFieldValues, TName>,
    meta: FormFieldRenderMeta
  ) => ReactElement;
}

/**
 * Minimal field wrapper for drawer migrations — pair with `useForm` + `zodResolver(schema)`.
 *
 * Every field gets a stable `id` (derived from its RHF `name`), matching `aria-invalid` and
 * `aria-describedby` (pointing at the description/error text), so labels are actually linked to
 * their control and `FormErrorSummary` can focus/scroll to the right element. The rendered
 * control receives these automatically via `cloneElement` — no call site changes required —
 * but a child can still opt out by setting its own `id`/`aria-*` explicitly.
 */
export function FormField<
  TFieldValues extends FieldValues = FieldValues,
  TName extends FieldPath<TFieldValues> = FieldPath<TFieldValues>
>({ name, label, className, description, children }: FormFieldProps<TFieldValues, TName>) {
  const {
    control,
    formState: { errors }
  } = useFormContext<TFieldValues>();
  const error = errors[name]?.message as string | undefined;

  const fieldId = formFieldId(name);
  const errorId = error ? formFieldErrorId(name) : undefined;
  const descriptionId = description ? formFieldDescriptionId(name) : undefined;
  const describedBy = [descriptionId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <div className={cn("space-y-1.5", className)}>
      <Label htmlFor={fieldId}>{label}</Label>
      {description ? (
        <p id={descriptionId} className="text-xs text-muted-foreground">
          {description}
        </p>
      ) : null}
      <Controller
        name={name}
        control={control}
        render={({ field }) => {
          const meta: FormFieldRenderMeta = { id: fieldId, invalid: Boolean(error), describedBy };
          const rendered = children(field, meta);
          if (!isValidElement(rendered)) return rendered;
          const existingProps = rendered.props as Record<string, unknown>;
          return cloneElement(rendered, {
            id: existingProps.id ?? fieldId,
            "aria-invalid": existingProps["aria-invalid"] ?? (error ? true : undefined),
            "aria-describedby": existingProps["aria-describedby"] ?? describedBy
          } as Record<string, unknown>);
        }}
      />
      {error ? (
        <p id={errorId} className="text-xs text-destructive" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** Convenience hook for the common `useForm({ resolver: zodResolver(schema) })` pattern. */
export function useZodForm<TInput extends FieldValues, TOutput extends FieldValues = TInput>(
  schema: z.ZodType<TOutput, TInput>,
  options?: Omit<UseFormProps<TInput, unknown, TOutput>, "resolver">
): UseFormReturn<TInput, unknown, TOutput> {
  return useForm<TInput, unknown, TOutput>({
    ...options,
    resolver: zodResolver(schema)
  });
}
