import { AutoComplete, Typography } from "antd";
import { CRON_PRESET_OPTIONS } from "../lib/cron";

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
    <AutoComplete
      value={value ?? ""}
      onChange={onChange}
      options={CRON_PRESET_OPTIONS}
      placeholder={placeholder}
      className="mono-input"
      style={{ width: "100%" }}
      filterOption={(inputValue, option) =>
        String(option?.label ?? "")
          .toLowerCase()
          .includes(inputValue.toLowerCase()) || String(option?.value ?? "").includes(inputValue)
      }
    />
    <Typography.Text type="secondary">
      {allowEmpty ? "Leave blank for manual-only. " : ""}
      {CRON_HELP_TEXT}
    </Typography.Text>
  </>
);
