package catalog

import (
	"context"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/catalog"
	"github.com/apache/iceberg-go/table"
	"github.com/apache/iceberg-go/utils"
	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"

	_ "github.com/apache/iceberg-go/catalog/hive"
	_ "github.com/apache/iceberg-go/catalog/rest"
	_ "github.com/apache/iceberg-go/io/gocloud"

	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
	"github.com/cryo-lake/cryo/engine-go/internal/testhook"
)

// catalogLoadAttempts/catalogLoadBackoff: a production cluster showed catalog.Load failing
// ~85-90% of the time with "connect:
// connection timed out" against a Hive Metastore, while raw TCP connectivity to the same host
// tested 100% reliable under both sequential and concurrent load from the same pods -- the
// failure isn't network/DNS, it's the metastore's own Thrift server-side thread pool
// intermittently saturated (shared with other consumers outside Cryo). Since each independent
// attempt has real, roughly constant odds of success, a few retries meaningfully raise the
// end-to-end success rate without needing the metastore itself fixed.
const catalogLoadAttempts = 4

var catalogLoadBackoff = []time.Duration{2 * time.Second, 4 * time.Second, 8 * time.Second}

// hiveConnectTimeoutErrSubstring must match connectWithTimeout's error text in
// third_party/iceberg-go-v0.6.0-patched/catalog/hive/client.go ("connect timed out after %s").
// Duplicated as a string rather than imported because this package (internal/catalog) sits
// above the vendored hive client and importing it here would be a layering inversion -- the
// substring match is the intentional, narrow coupling point instead.
const hiveConnectTimeoutErrSubstring = "connect timed out after"

// isMetastoreUnreachable reports whether err is the bounded connect-timeout error added
// 2026-08-21 (client.go's connectWithTimeout), as opposed to a fast Thrift-level rejection.
//
// Found live 2026-08-24: after that fix landed, catalogLoadAttempts' 4-attempt design -- built
// for the transient-overload failure mode described above, where each attempt is cheap and
// fails fast -- was silently being applied to a *different* failure mode it was never tuned
// for: a sustained metastore outage where every attempt burns the full 45s timeout. 739 job
// failures in 24h each showed all 4 attempts timing out at the full 45s, for an average total
// duration of 4,103s (68min) and a max of 14,424s (4h) per table -- and since native batch pods
// run tables fully serially (cryo/engines/native/batch.py's execute_batch, no early-exit), a
// 32-table batch hitting this could burn over an hour and a half before even reaching its
// healthy tables. A timed-out attempt is a much stronger "unreachable right now" signal than a
// fast rejection, so retrying it 3 more times at the same 45s cost each is pure waste for this
// table and everything queued behind it in the same batch.
func isMetastoreUnreachable(err error) bool {
	return err != nil && strings.Contains(err.Error(), hiveConnectTimeoutErrSubstring)
}

// LoadTable opens the REST (or configured) catalog and loads the target table.
func LoadTable(ctx context.Context, req *protocol.ExecuteRequest) (catalog.Catalog, *table.Table, error) {
	props := buildProperties(req)
	loader := func(ctx context.Context) (catalog.Catalog, error) {
		return catalog.Load(ctx, "cryo", props)
	}
	cat, err := loadCatalogWithRetry(ctx, loader, catalogLoadAttempts, catalogLoadBackoff)
	if err != nil {
		return nil, nil, fmt.Errorf("failed to connect to catalog: %w", err)
	}
	cat = testhook.WrapCatalog(cat)

	ident, err := ParseTableIdent(req.TableRef)
	if err != nil {
		return nil, nil, err
	}

	tbl, err := cat.LoadTable(ctx, ident)
	if err != nil {
		return nil, nil, fmt.Errorf("failed to load table: %w", err)
	}

	return cat, tbl, nil
}

// loadCatalogWithRetry retries a catalog-connect function on failure. loader is injected so the
// retry/backoff behavior itself is unit-testable without a real catalog endpoint.
//
// Stops early -- without spending the remaining attempts/backoff -- the first time loader
// returns a bounded connect-timeout error (isMetastoreUnreachable): see that function's
// docstring for the live incident this fixes. Any other error keeps the original
// transient-overload retry behavior unchanged.
func loadCatalogWithRetry(
	ctx context.Context,
	loader func(context.Context) (catalog.Catalog, error),
	attempts int,
	backoff []time.Duration,
) (catalog.Catalog, error) {
	var lastErr error
	attemptsMade := 0
	for attempt := 0; attempt < attempts; attempt++ {
		if attempt > 0 {
			select {
			case <-ctx.Done():
				return nil, ctx.Err()
			case <-time.After(backoff[attempt-1]):
			}
		}
		cat, err := loader(ctx)
		attemptsMade++
		if err == nil {
			return cat, nil
		}
		lastErr = err
		if isMetastoreUnreachable(err) {
			break
		}
	}
	return nil, fmt.Errorf("after %d attempts: %w", attemptsMade, lastErr)
}

// WithSourceAWSConfig attaches an explicit static-credential aws.Config to ctx when the request
// carries S3 access keys, and returns ctx unchanged otherwise (so IRSA / instance-profile
// deployments keep working via the default AWS chain).
//
// Why this is required: iceberg-go's commit path (catalog.CommitTable -> internal.WriteMetadata)
// rebuilds the S3 FileIO from the *table* metadata properties, which do NOT carry Cryo's
// per-source S3 credentials — only the read/scan FileIO is built from catalog properties. Without
// this, the commit that writes the new `*.metadata.json` falls back to the default AWS credential
// chain, which in role-based environments attempts an STS AssumeRole and fails with
// `InvalidClientTokenId`. gocloud's createS3Bucket checks utils.GetAwsConfig(ctx) before that
// fallback, so seeding the context makes both the scan and the metadata write use the same static
// credentials. Endpoint/path-style are still read from properties (or AWS_S3_ENDPOINT), so custom
// endpoints (e.g. MinIO) are propagated via the env var for the property-less write path.
func WithSourceAWSConfig(ctx context.Context, req *protocol.ExecuteRequest) context.Context {
	accessKey := firstNonEmpty(deref(req.S3AccessKey), req.CatalogProperties["s3.access-key-id"])
	secretKey := firstNonEmpty(deref(req.S3SecretKey), req.CatalogProperties["s3.secret-access-key"])
	region := sourceRegion(req)
	if accessKey == "" || secretKey == "" {
		if region == "" {
			return ctx
		}
		cfg, err := awsconfig.LoadDefaultConfig(ctx, awsconfig.WithRegion(region))
		if err != nil {
			return ctx
		}
		return utils.WithAwsConfig(ctx, &cfg)
	}
	sessionToken := req.CatalogProperties["s3.session-token"]

	cfg := aws.Config{
		Credentials: credentials.NewStaticCredentialsProvider(accessKey, secretKey, sessionToken),
		Region:      firstNonEmpty(region, "us-east-1"),
	}
	if endpoint := firstNonEmpty(deref(req.S3Endpoint), req.CatalogProperties["s3.endpoint"]); endpoint != "" {
		// The commit write path has no s3.endpoint in table properties; gocloud falls back to
		// this env var, so a custom endpoint (MinIO) still reaches the metadata write.
		_ = os.Setenv("AWS_S3_ENDPOINT", endpoint)
	}
	return utils.WithAwsConfig(ctx, &cfg)
}

func sourceRegion(req *protocol.ExecuteRequest) string {
	return firstNonEmpty(
		req.CatalogProperties["s3.region"],
		req.CatalogProperties["glue.region"],
		req.CatalogProperties["client.region"],
		os.Getenv("AWS_REGION"),
		os.Getenv("AWS_DEFAULT_REGION"),
	)
}

func deref(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}

func buildProperties(req *protocol.ExecuteRequest) iceberg.Properties {
	props := iceberg.Properties{
		"uri":       req.CatalogURI,
		"warehouse": req.Warehouse,
	}
	if req.S3Endpoint != nil && *req.S3Endpoint != "" {
		props["s3.endpoint"] = *req.S3Endpoint
	}
	if req.S3AccessKey != nil && *req.S3AccessKey != "" {
		props["s3.access-key-id"] = *req.S3AccessKey
	}
	if req.S3SecretKey != nil && *req.S3SecretKey != "" {
		props["s3.secret-access-key"] = *req.S3SecretKey
	}
	for key, value := range req.CatalogProperties {
		if value != "" {
			props[key] = value
		}
	}
	// Resolve the iceberg-go registry key last so it wins over any stray "type" in
	// catalog_properties. Hive Metastore speaks Thrift; iceberg-go registers it under
	// "hive" (not the "thrift" scheme), so an unresolved thrift:// URI would otherwise
	// be loaded as a REST catalog and fail with `unsupported protocol scheme "thrift"`.
	explicitType := req.CatalogType
	if explicitType == "" {
		explicitType = props["type"]
	}
	props["type"] = ResolveCatalogType(explicitType, req.CatalogURI)
	return props
}

// ResolveCatalogType picks the iceberg-go catalog registry key for a request. An explicit
// catalog type (from Cryo's source config) wins; otherwise it is inferred from the catalog
// URI scheme (thrift -> hive, http/https -> rest). Unrecognized explicit types are trusted
// as-is so custom registered catalogs still work; everything else defaults to "rest".
func ResolveCatalogType(catalogType, uri string) string {
	if raw := strings.TrimSpace(catalogType); raw != "" {
		if normalized := normalizeCatalogType(raw); normalized != "" {
			return normalized
		}
		return strings.ToLower(raw)
	}
	if scheme, _, ok := strings.Cut(uri, "://"); ok {
		if normalized := normalizeCatalogType(scheme); normalized != "" {
			return normalized
		}
	}
	return "rest"
}

func normalizeCatalogType(raw string) string {
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case "hive", "hms", "hive-metastore", "hive_metastore", "thrift":
		return "hive"
	case "glue", "aws-glue", "aws_glue":
		return "glue"
	case "rest", "iceberg-rest", "iceberg_rest", "polaris", "nessie", "http", "https":
		return "rest"
	default:
		return ""
	}
}

// ParseTableIdent mirrors cryo-engine parse_table_ident.
func ParseTableIdent(tableRef string) (table.Identifier, error) {
	parts := strings.Split(tableRef, ".")
	if len(parts) < 2 {
		return nil, fmt.Errorf(
			"invalid table_ref: %s (expected namespace.table or catalog.namespace.table)",
			tableRef,
		)
	}
	tableName := parts[len(parts)-1]
	var namespaceParts []string
	if len(parts) >= 3 {
		namespaceParts = parts[1 : len(parts)-1]
	} else {
		namespaceParts = []string{parts[0]}
	}
	return catalog.ToIdentifier(append(namespaceParts, tableName)...), nil
}
