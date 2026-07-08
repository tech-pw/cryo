package fixtures

import (
	"os"

	"github.com/apache/iceberg-go"
)

const (
	Namespace       = "native_compaction"
	MORPosTable     = "f_mor_pos"
	MOREqTable      = "f_mor_eq"
	MORMixedTable   = "f_mor_mixed"
	MORPartialTable = "f_mor_partial"
)

// Config mirrors tests/integration/native/rest_env.py environment variables.
type Config struct {
	RESTURI    string
	Warehouse  string
	S3Endpoint string
	S3Access   string
	S3Secret   string
	S3Region   string
}

func ConfigFromEnv() Config {
	return Config{
		RESTURI:    envOr("CRYO_REST_URI", "http://localhost:8181"),
		Warehouse:  envOr("ICEBERG_WAREHOUSE", "s3://warehouse/"),
		S3Endpoint: envOr("S3_ENDPOINT", "http://localhost:9000"),
		S3Access:   envOr("AWS_ACCESS_KEY_ID", "minioadmin"),
		S3Secret:   envOr("AWS_SECRET_ACCESS_KEY", "minioadmin"),
		S3Region:   envOr("AWS_REGION", "us-east-1"),
	}
}

func (c Config) CatalogProperties() iceberg.Properties {
	return iceberg.Properties{
		"type":                 "rest",
		"uri":                  c.RESTURI,
		"warehouse":            c.Warehouse,
		"s3.endpoint":          c.S3Endpoint,
		"s3.access-key-id":     c.S3Access,
		"s3.secret-access-key": c.S3Secret,
		"s3.path-style-access": "true",
		"s3.region":            c.S3Region,
	}
}

func envOr(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
