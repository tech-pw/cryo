import { X } from "lucide-react";
import { type KeyboardEvent } from "react";
import { cn } from "../../lib/cn";
import { Badge } from "./badge";
import { Input } from "./input";

export interface TagsInputProps {
  value?: string[];
  onChange?: (value: string[]) => void;
  placeholder?: string;
  tokenSeparators?: string[];
  className?: string;
}

export const TagsInput = ({
  value = [],
  onChange,
  placeholder,
  tokenSeparators = [","],
  className
}: TagsInputProps) => {
  const addTokens = (raw: string) => {
    const parts = tokenSeparators.reduce(
      (acc, separator) => acc.flatMap((part) => part.split(separator)),
      [raw] as string[]
    );
    const next = [...value];
    parts
      .map((part) => part.trim())
      .filter(Boolean)
      .forEach((part) => {
        if (!next.includes(part)) next.push(part);
      });
    onChange?.(next);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" || tokenSeparators.includes(event.key)) {
      event.preventDefault();
      addTokens(event.currentTarget.value);
      event.currentTarget.value = "";
    }
  };

  return (
    <div
      className={cn(
        "flex min-h-[var(--control-height)] flex-wrap items-center gap-1 rounded-[var(--radius-default)] border border-border bg-background px-2 py-1",
        className
      )}
    >
      {value.map((tag) => (
        <Badge key={tag} variant="secondary" className="gap-1">
          {tag}
          <button
            type="button"
            className="rounded-sm hover:bg-background/60"
            aria-label={`Remove ${tag}`}
            onClick={() => onChange?.(value.filter((item) => item !== tag))}
          >
            <X className="size-3" />
          </button>
        </Badge>
      ))}
      <Input
        className="min-w-[8rem] flex-1 border-0 px-1 shadow-none focus-visible:ring-0"
        placeholder={placeholder}
        onKeyDown={onKeyDown}
        onBlur={(event) => {
          if (event.currentTarget.value.trim()) {
            addTokens(event.currentTarget.value);
            event.currentTarget.value = "";
          }
        }}
      />
    </div>
  );
};
