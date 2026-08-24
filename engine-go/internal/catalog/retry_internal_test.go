package catalog

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	icatalog "github.com/apache/iceberg-go/catalog"
)

// fakeCatalog satisfies icatalog.Catalog minimally enough to be returned by a test loader --
// loadCatalogWithRetry never calls any of its methods, it just needs a non-nil value to prove
// which attempt's result was returned.
type fakeCatalog struct{ icatalog.Catalog }

func TestLoadCatalogWithRetrySucceedsOnFirstAttempt(t *testing.T) {
	calls := 0
	loader := func(context.Context) (icatalog.Catalog, error) {
		calls++
		return &fakeCatalog{}, nil
	}

	cat, err := loadCatalogWithRetry(context.Background(), loader, 4, nil)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cat == nil {
		t.Fatal("expected non-nil catalog")
	}
	if calls != 1 {
		t.Fatalf("expected 1 call, got %d", calls)
	}
}

func TestLoadCatalogWithRetrySucceedsAfterTransientFailures(t *testing.T) {
	calls := 0
	loader := func(context.Context) (icatalog.Catalog, error) {
		calls++
		if calls < 3 {
			return nil, errors.New("connect: connection timed out")
		}
		return &fakeCatalog{}, nil
	}
	backoff := []time.Duration{time.Millisecond, time.Millisecond, time.Millisecond}

	cat, err := loadCatalogWithRetry(context.Background(), loader, 4, backoff)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cat == nil {
		t.Fatal("expected non-nil catalog")
	}
	if calls != 3 {
		t.Fatalf("expected 3 calls (2 failures + 1 success), got %d", calls)
	}
}

func TestLoadCatalogWithRetryExhaustsAttemptsAndReturnsLastError(t *testing.T) {
	calls := 0
	loader := func(context.Context) (icatalog.Catalog, error) {
		calls++
		return nil, errors.New("connect: connection timed out")
	}
	backoff := []time.Duration{time.Millisecond, time.Millisecond, time.Millisecond}

	cat, err := loadCatalogWithRetry(context.Background(), loader, 4, backoff)

	if err == nil {
		t.Fatal("expected an error after exhausting attempts")
	}
	if cat != nil {
		t.Fatal("expected nil catalog on failure")
	}
	if calls != 4 {
		t.Fatalf("expected exactly 4 attempts, got %d", calls)
	}
}

func TestLoadCatalogWithRetryRespectsContextCancellation(t *testing.T) {
	calls := 0
	ctx, cancel := context.WithCancel(context.Background())
	loader := func(context.Context) (icatalog.Catalog, error) {
		calls++
		if calls == 1 {
			cancel()
		}
		return nil, errors.New("connect: connection timed out")
	}
	backoff := []time.Duration{time.Second, time.Second, time.Second}

	_, err := loadCatalogWithRetry(ctx, loader, 4, backoff)

	if !errors.Is(err, context.Canceled) {
		t.Fatalf("expected context.Canceled, got %v", err)
	}
	if calls != 1 {
		t.Fatalf("expected retry loop to stop after cancellation, got %d calls", calls)
	}
}

// TestLoadCatalogWithRetryStopsEarlyOnMetastoreTimeout is a direct regression test for the live
// incident isMetastoreUnreachable's docstring describes: 739 job failures in 24h each burning
// all 4 attempts at the full 45s connect-timeout cap against a sustained metastore outage,
// averaging 4,103s (68min) per table. A bounded connect-timeout error must stop the retry loop
// immediately instead of spending the remaining attempts (and their backoff) on a failure mode
// that a timeout has already strongly signaled won't recover within this call.
func TestLoadCatalogWithRetryStopsEarlyOnMetastoreTimeout(t *testing.T) {
	calls := 0
	loader := func(context.Context) (icatalog.Catalog, error) {
		calls++
		return nil, errors.New("failed to connect to metastore: connect timed out after 45s")
	}
	backoff := []time.Duration{time.Millisecond, time.Millisecond, time.Millisecond}

	_, err := loadCatalogWithRetry(context.Background(), loader, 4, backoff)

	if err == nil {
		t.Fatal("expected an error")
	}
	if calls != 1 {
		t.Fatalf("expected exactly 1 call (early stop on timeout), got %d", calls)
	}
	if !strings.Contains(err.Error(), "after 1 attempts") {
		t.Fatalf("expected error to report the true attempt count, got: %v", err)
	}
}

// TestLoadCatalogWithRetryStillRetriesNonTimeoutErrorsFully guards the other direction: a fast
// Thrift-level rejection (the original transient-overload failure mode catalogLoadAttempts was
// built for) must still exhaust all attempts as before -- this fix only special-cases the
// bounded-timeout error shape, it must not change behavior for anything else.
func TestLoadCatalogWithRetryStillRetriesNonTimeoutErrorsFully(t *testing.T) {
	calls := 0
	loader := func(context.Context) (icatalog.Catalog, error) {
		calls++
		return nil, errors.New("connect: connection timed out")
	}
	backoff := []time.Duration{time.Millisecond, time.Millisecond, time.Millisecond}

	_, err := loadCatalogWithRetry(context.Background(), loader, 4, backoff)

	if err == nil {
		t.Fatal("expected an error after exhausting attempts")
	}
	if calls != 4 {
		t.Fatalf("expected all 4 attempts for a non-timeout error, got %d", calls)
	}
}

// TestLoadCatalogWithRetryTimeoutAfterTransientFailuresStillStopsEarly covers the mixed case:
// a table that failed fast once (Thrift busy) then hits a real timeout must still stop rather
// than burning out the remaining attempts, since the timeout itself is the strong signal here
// regardless of what came before it.
func TestLoadCatalogWithRetryTimeoutAfterTransientFailuresStillStopsEarly(t *testing.T) {
	calls := 0
	loader := func(context.Context) (icatalog.Catalog, error) {
		calls++
		if calls == 1 {
			return nil, errors.New("connect: connection timed out")
		}
		return nil, errors.New("failed to connect to metastore: connect timed out after 45s")
	}
	backoff := []time.Duration{time.Millisecond, time.Millisecond, time.Millisecond}

	_, err := loadCatalogWithRetry(context.Background(), loader, 4, backoff)

	if err == nil {
		t.Fatal("expected an error")
	}
	if calls != 2 {
		t.Fatalf("expected 2 calls (1 transient + 1 timeout that stops the loop), got %d", calls)
	}
}

func TestIsMetastoreUnreachable(t *testing.T) {
	cases := []struct {
		name string
		err  error
		want bool
	}{
		{"nil error", nil, false},
		{
			"bounded connect timeout",
			errors.New("failed to connect to metastore: connect timed out after 45s"),
			true,
		},
		{"fast thrift rejection", errors.New("connect: connection refused"), false},
		{"generic transient failure", errors.New("connect: connection timed out"), false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := isMetastoreUnreachable(tc.err); got != tc.want {
				t.Fatalf("isMetastoreUnreachable(%v) = %v, want %v", tc.err, got, tc.want)
			}
		})
	}
}
