export interface SlackFormValues {
  name: string;
  webhook_url: string;
  channel: string;
  daily_report_enabled: boolean;
  daily_report_cron: string;
  daily_report_use_ai: boolean;
  enabled: boolean;
  is_default: boolean;
}
