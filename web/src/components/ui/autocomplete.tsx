import { useMemo, useState } from "react";
import { cn } from "../../lib/cn";
import { Input } from "./input";

export interface AutocompleteOption {
  label?: string;
  value: string;
}

export interface AutocompleteProps {
  value?: string;
  onChange?: (value: string) => void;
  options: AutocompleteOption[];
  placeholder?: string;
  className?: string;
  filterOption?: (input: string, option: AutocompleteOption) => boolean;
}

export const Autocomplete = ({
  value = "",
  onChange,
  options,
  placeholder,
  className,
  filterOption
}: AutocompleteProps) => {
  const [open, setOpen] = useState(false);
  const filtered = useMemo(() => {
    const matcher =
      filterOption ??
      ((input, option) =>
        option.label?.toLowerCase().includes(input.toLowerCase()) || option.value.includes(input));
    return options.filter((option) => matcher(value, option));
  }, [filterOption, options, value]);

  return (
    <div className={cn("relative w-full", className)}>
      <Input
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange?.(event.target.value)}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
      />
      {open && filtered.length > 0 ? (
        <ul
          className="absolute z-50 mt-1 max-h-48 w-full overflow-auto rounded-[var(--radius-default)] border border-border bg-background py-1 shadow-md"
          role="listbox"
        >
          {filtered.map((option) => (
            <li key={option.value}>
              <button
                type="button"
                className="w-full px-3 py-1.5 text-left text-sm hover:bg-[var(--color-surface-sunken)]"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => {
                  onChange?.(option.value);
                  setOpen(false);
                }}
              >
                {option.label ?? option.value}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
};
