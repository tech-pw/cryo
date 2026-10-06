import { KNOWN_SOURCE_KEYS, REDACTED_PLACEHOLDER, type SourceFormValues } from "./constants";
import { isRedactedPlaceholder } from "./helpers";

export const decomposeSourceConfig = (config: Record<string, unknown> | undefined) => {
  const cfg = config ?? {};
  const advanced = Object.fromEntries(Object.entries(cfg).filter(([key]) => !KNOWN_SOURCE_KEYS.includes(key)));

  let s3_auth_method: "default" | "access_key" | "assume_role" = "default";
  if (cfg["s3.role-arn"]) {
    s3_auth_method = "assume_role";
  } else if (cfg["s3.access-key-id"]) {
    s3_auth_method = "access_key";
  }

  const rawSecretAccessKey = cfg["s3.secret-access-key"];
  // The API always redacts this key (see cryo/api/redact.py) once a source has been saved with
  // one, so never pre-fill the editable input with the literal "***REDACTED***" placeholder --
  // show a blank field with a "leave blank to keep it" hint instead (see SourceDrawer.tsx).
  const s3_has_secret_access_key = typeof rawSecretAccessKey === "string" && rawSecretAccessKey.length > 0;
  const s3_secret_access_key =
    typeof rawSecretAccessKey === "string" && !isRedactedPlaceholder(rawSecretAccessKey)
      ? rawSecretAccessKey
      : undefined;

  return {
    uri: typeof cfg.uri === "string" ? cfg.uri : undefined,
    warehouse: typeof cfg.warehouse === "string" ? cfg.warehouse : undefined,
    catalog_name: typeof cfg.catalog_name === "string" ? cfg.catalog_name : undefined,
    s3_auth_method,
    s3_endpoint: typeof cfg["s3.endpoint"] === "string" ? (cfg["s3.endpoint"] as string) : undefined,
    s3_region: typeof cfg["s3.region"] === "string" ? (cfg["s3.region"] as string) : undefined,
    s3_access_key_id: typeof cfg["s3.access-key-id"] === "string" ? (cfg["s3.access-key-id"] as string) : undefined,
    s3_secret_access_key,
    s3_has_secret_access_key,
    s3_role_arn: typeof cfg["s3.role-arn"] === "string" ? (cfg["s3.role-arn"] as string) : undefined,
    s3_external_id: typeof cfg["s3.external-id"] === "string" ? (cfg["s3.external-id"] as string) : undefined,
    s3_role_session_name:
      typeof cfg["s3.role-session-name"] === "string" ? (cfg["s3.role-session-name"] as string) : undefined,
    s3_path_style_access: cfg["s3.path-style-access"] === "true" || cfg["s3.path-style-access"] === true,
    advanced_json: Object.keys(advanced).length ? JSON.stringify(advanced, null, 2) : ""
  };
};

export const buildSourceConfig = (values: SourceFormValues): Record<string, unknown> => {
  let extra: Record<string, unknown> = {};
  if (values.advanced_json?.trim()) {
    extra = JSON.parse(values.advanced_json) as Record<string, unknown>;
  }
  const config: Record<string, unknown> = { ...extra };
  // Always send known keys. Omitted keys survive unredact_merge, so clearing a field
  // or switching auth methods would otherwise snap back to the stored value.
  config.uri = values.uri || null;
  config.warehouse = values.warehouse || null;
  config.catalog_name = values.catalog_name || null;
  config["s3.endpoint"] = values.s3_endpoint || null;
  config["s3.region"] = values.s3_region || null;
  config["s3.path-style-access"] = values.s3_path_style_access ? "true" : null;

  if (values.s3_auth_method === "access_key") {
    config["s3.access-key-id"] = values.s3_access_key_id || null;
    if (values.s3_secret_access_key) {
      config["s3.secret-access-key"] = values.s3_secret_access_key;
    } else if (values.s3_has_secret_access_key) {
      config["s3.secret-access-key"] = REDACTED_PLACEHOLDER;
    } else {
      config["s3.secret-access-key"] = null;
    }
    config["s3.role-arn"] = null;
    config["s3.external-id"] = null;
    config["s3.role-session-name"] = null;
  } else if (values.s3_auth_method === "assume_role") {
    config["s3.role-arn"] = values.s3_role_arn || null;
    config["s3.external-id"] = values.s3_external_id || null;
    config["s3.role-session-name"] = values.s3_role_session_name || null;
    config["s3.access-key-id"] = null;
    config["s3.secret-access-key"] = null;
  } else {
    config["s3.access-key-id"] = null;
    config["s3.secret-access-key"] = null;
    config["s3.role-arn"] = null;
    config["s3.external-id"] = null;
    config["s3.role-session-name"] = null;
  }

  return config;
};
