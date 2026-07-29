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
import type { ReactElement, ReactNode } from "react";
import type { z } from "zod";

import { cn } from "../../lib/cn";
import { Label } from "./label";

export { FormProvider, useForm, useFormContext, zodResolver };
export type { UseFormProps, UseFormReturn };

export interface FormFieldProps<
  TFieldValues extends FieldValues = FieldValues,
  TName extends FieldPath<TFieldValues> = FieldPath<TFieldValues>
> {
  name: TName;
  label: ReactNode;
  className?: string;
  description?: string;
  children: (field: ControllerRenderProps<TFieldValues, TName>) => ReactElement;
}

/** Minimal field wrapper for drawer migrations — pair with `useForm` + `zodResolver(schema)`. */
export function FormField<
  TFieldValues extends FieldValues = FieldValues,
  TName extends FieldPath<TFieldValues> = FieldPath<TFieldValues>
>({ name, label, className, description, children }: FormFieldProps<TFieldValues, TName>) {
  const {
    control,
    formState: { errors }
  } = useFormContext<TFieldValues>();
  const error = errors[name]?.message as string | undefined;

  return (
    <div className={cn("space-y-1.5", className)}>
      <Label htmlFor={name}>{label}</Label>
      {description ? <p className="text-xs text-muted-foreground">{description}</p> : null}
      <Controller
        name={name}
        control={control}
        render={({ field }) => children(field)}
      />
      {error ? (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** Convenience hook for the common `useForm({ resolver: zodResolver(schema) })` pattern. */
export function useZodForm<TSchema extends z.ZodTypeAny>(
  schema: TSchema,
  options?: Omit<UseFormProps<z.infer<TSchema>>, "resolver">
): UseFormReturn<z.infer<TSchema>> {
  return useForm<z.infer<TSchema>>({
    ...options,
    resolver: zodResolver(schema)
  });
}
