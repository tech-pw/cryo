import { describe, expect, it } from "vitest";
import type { DestinationRead, SourceRead } from "../../api/types";
import { destinationDetails, offpeakLabel, redactUri, sourceDetails } from "./connectionSummary";

const source = (config: SourceRead["config"]): SourceRead => ({
  id: "src-1",
  name: "cdp",
  type: "hive",
  config,
  enabled: true,
  maintenance_schema_names: [],
  created_at: "",
  updated_at: ""
});

const destination = (engine: DestinationRead["engine"], config: DestinationRead["config"]): DestinationRead => ({
  id: "dest-1",
  name: "engine",
  engine,
  config,
  enabled: true,
  offpeak_start: 1,
  offpeak_end: 7,
  created_at: "",
  updated_at: ""
});

describe("connection summaries", () => {
  it("strips credentials embedded in a URI", () => {
    expect(redactUri("https://user:pa55@catalog.internal/api")).toBe("https://catalog.internal/api");
    expect(redactUri("thrift://hms:9083")).toBe("thrift://hms:9083");
  });

  it("summarises a source without rendering secret values", () => {
    const details = sourceDetails(
      source({
        uri: "thrift://admin:hunter2@hms:9083",
        warehouse: "s3://lake/wh",
        "s3.region": "ap-south-1",
        "s3.access-key-id": "AKIAEXAMPLE",
        "s3.secret-access-key": "***REDACTED***"
      })
    );
    expect(details).toEqual([
      { label: "Endpoint", value: "thrift://hms:9083", mono: true },
      { label: "Warehouse", value: "s3://lake/wh", mono: true },
      { label: "Region", value: "ap-south-1" },
      { label: "S3 auth", value: "Static access keys" }
    ]);
    expect(JSON.stringify(details)).not.toMatch(/AKIA|hunter2|REDACTED/);
  });

  it("names the S3 auth mode from the stored keys", () => {
    expect(sourceDetails(source({ "s3.role-arn": "arn:aws:iam::1:role/x" })).at(-1)?.value).toBe("Assume IAM role");
    expect(sourceDetails(source({})).at(-1)?.value).toBe("Default credentials");
  });

  it("shows only coordinators that are actually stored for Trino", () => {
    expect(destinationDetails(destination("trino", {})).map((d) => d.label)).toEqual(["Off-peak"]);
    expect(
      destinationDetails(
        destination("trino", { coordinators: [{ host: "a", port: 8080 }, { host: "b", port: 8443 }], catalog: "cdp" })
      ).slice(0, 2)
    ).toEqual([
      { label: "Coordinator", value: "a:8080 +1", mono: true },
      { label: "Catalog", value: "cdp", mono: true }
    ]);
  });

  it("formats the off-peak window", () => {
    expect(offpeakLabel({ offpeak_start: 1, offpeak_end: 7 })).toBe("01:00–07:00 IST");
    expect(offpeakLabel({ offpeak_start: 3, offpeak_end: 3 })).toBe("Any time");
    expect(offpeakLabel({ offpeak_start: null, offpeak_end: 7 })).toBe("Any time");
  });
});
