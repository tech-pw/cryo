/**
 * Constants and form-value types for the Connections page.
 *
 * Extracted from the formerly-monolithic Connections.tsx so the page can focus
 * on data fetching and tab assembly.
 */

export const REDACTED_PLACEHOLDER = "***REDACTED***";

export const CATALOG_TYPES = [
  { value: "rest", label: "REST catalog" },
  { value: "hive", label: "Hive Metastore" },
  { value: "glue", label: "AWS Glue" },
  { value: "polaris", label: "Apache Polaris" },
  { value: "nessie", label: "Project Nessie" }
];

export const CATALOG_URI_HINT: Record<string, string> = {
  rest: "http://localhost:8181",
  hive: "thrift://localhost:9083",
  glue: "",
  polaris: "http://localhost:8181/api/catalog",
  nessie: "http://localhost:19120/api/v1"
};

export const KNOWN_SOURCE_KEYS = [
  "uri",
  "warehouse",
  "catalog_name",
  "type",
  "s3.endpoint",
  "s3.access-key-id",
  "s3.secret-access-key",
  "s3.path-style-access",
  "s3.region",
  "s3.role-arn",
  "s3.external-id",
  "s3.role-session-name"
];

export const S3_AUTH_METHODS = [
  { value: "default", label: "Default credentials (IAM Role / Instance Profile / Environment)" },
  { value: "access_key", label: "Access Key + Secret Key" },
  { value: "assume_role", label: "Assume IAM Role (cross-account)" }
];

export const AWS_REGIONS = [
  "us-east-1",
  "us-east-2",
  "us-west-1",
  "us-west-2",
  "af-south-1",
  "ap-east-1",
  "ap-south-1",
  "ap-south-2",
  "ap-southeast-1",
  "ap-southeast-2",
  "ap-southeast-3",
  "ap-southeast-4",
  "ap-northeast-1",
  "ap-northeast-2",
  "ap-northeast-3",
  "ca-central-1",
  "eu-central-1",
  "eu-central-2",
  "eu-west-1",
  "eu-west-2",
  "eu-west-3",
  "eu-north-1",
  "eu-south-1",
  "eu-south-2",
  "me-central-1",
  "me-south-1",
  "sa-east-1"
].map((region) => ({ value: region }));

export const SCAN_ACTIVE_STATUSES = new Set(["accepted", "queued", "running"]);

export interface SourceFormValues {
  name: string;
  type: string;
  uri?: string;
  warehouse?: string;
  catalog_name?: string;
  s3_auth_method: "default" | "access_key" | "assume_role";
  s3_endpoint?: string;
  s3_region?: string;
  s3_access_key_id?: string;
  s3_secret_access_key?: string;
  /** True when the saved config already has a secret access key (not re-sent by the API — see redact.py). */
  s3_has_secret_access_key?: boolean;
  s3_role_arn?: string;
  s3_external_id?: string;
  s3_role_session_name?: string;
  s3_path_style_access?: boolean;
  advanced_json?: string;
  secret_ref?: string | null;
  enabled: boolean;
  maintenance_schema_names?: string[];
}