import { CRON_PRESET_OPTIONS } from "../lib/cron";
import { Autocomplete } from "./ui/autocomplete";

type CronInputProps = {
  value?: string | null;
  onChange?: (value: string) => void;
  placeholder?: string;
  allowEmpty?: boolean;
};

export const CRON_HELP_TEXT = "Five fields: minute hour day-of-month month day-of-week. Times are UTC.";

export const CronInput = ({
  value,
  onChange,
  placeholder = "0 2 * * *",
  allowEmpty = false
}: CronInputProps) => (
  <>
    <Autocomplete
      value={value ?? ""}
      onChange={onChange}
      options={CRON_PRESET_OPTIONS}
      placeholder={placeholder}
      className="mono-input"
      filterOption={(inputValue, option) =>
        (option.label?.toLowerCase().includes(inputValue.toLowerCase()) ?? false) ||
        option.value.includes(inputValue)
      }
    />
    <p className="mt-1 text-xs text-muted-foreground">
      {allowEmpty ? "Leave blank for manual-only. " : ""}
      {CRON_HELP_TEXT}
    </p>
  </>
);
