package catalog

import (
	"context"
	"errors"
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
