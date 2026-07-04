const MAX_CRON_EXPRESSION_LENGTH = 100;
const CRON_ALLOWED_CHARS = /^[0-9*/,\- ]+$/;

const CRON_FIELD_RANGES = [
  [0, 59],
  [0, 23],
  [1, 31],
  [1, 12],
  [0, 6]
] as const;

export const CRON_PRESET_OPTIONS = [
  { label: "Daily at 2 AM UTC", value: "0 2 * * *" },
  { label: "Daily at 8 AM UTC", value: "0 8 * * *" },
  { label: "Daily at 9 AM UTC", value: "0 9 * * *" },
  { label: "Daily at 10 AM UTC", value: "0 10 * * *" },
  { label: "Every 6 hours", value: "0 */6 * * *" },
  { label: "Every 12 hours", value: "0 */12 * * *" },
  { label: "Weekdays at 2 AM UTC", value: "0 2 * * 1-5" },
  { label: "Weekdays at 9 AM UTC", value: "0 9 * * 1-5" }
];

export const normalizeCronExpression = (expression: string): string =>
  expression.trim().split(/ +/).join(" ");

export const validateCronExpression = (
  expression: string | undefined | null,
  options: { allowEmpty?: boolean } = {}
): string | null => {
  if (!expression || !expression.trim()) {
    return options.allowEmpty ? null : "Cron expression is required";
  }

  if (expression.length > MAX_CRON_EXPRESSION_LENGTH) {
    return "Cron expression is too long";
  }

  if (!CRON_ALLOWED_CHARS.test(expression)) {
    return "Cron expression contains unsupported characters";
  }

  const fields = normalizeCronExpression(expression).split(" ");
  if (fields.length !== 5) {
    return "Cron expression must have five fields";
  }

  for (let index = 0; index < fields.length; index += 1) {
    const error = validateCronField(fields[index], CRON_FIELD_RANGES[index][0], CRON_FIELD_RANGES[index][1]);
    if (error) return error;
  }

  return null;
};

export const cronFormValidator =
  (options: { allowEmpty?: boolean } = {}) =>
  (_: unknown, value?: string): Promise<void> => {
    const error = validateCronExpression(value, options);
    return error ? Promise.reject(new Error(error)) : Promise.resolve();
  };

const validateCronField = (field: string, minimum: number, maximum: number): string | null => {
  if (field === "*") return null;

  if (field.includes(",")) {
    const parts = field.split(",");
    if (parts.includes("")) return "Cron lists cannot contain empty values";
    for (const part of parts) {
      const error = validateCronField(part, minimum, maximum);
      if (error) return error;
    }
    return null;
  }

  if (field.startsWith("*/")) {
    const stepText = field.slice(2);
    if (!/^\d+$/.test(stepText)) return "Cron step values must be integers";
    const step = Number(stepText);
    if (step <= 0) return "Cron step values must be positive";
    return null;
  }

  if (field.includes("-") && !field.startsWith("-")) {
    const [start, end] = field.split("-", 2);
    const startInt = Number(start);
    const endInt = Number(end);
    if (!Number.isInteger(startInt) || !Number.isInteger(endInt)) {
      return "Cron ranges must use integer values";
    }
    if (startInt > endInt) return "Cron ranges must be ascending";
    if (startInt < minimum || endInt > maximum) {
      return "Cron range is outside the supported bounds";
    }
    return null;
  }

  const value = Number(field);
  if (!Number.isInteger(value)) return "Cron values must be integers";
  if (value < minimum || value > maximum) {
    return "Cron value is outside the supported bounds";
  }
  return null;
};
