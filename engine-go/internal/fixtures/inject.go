package fixtures

import (
	"context"
	"fmt"
	"strconv"
	"strings"

	"github.com/apache/iceberg-go/table"
)

// CommitEqualityDeleteOnTable appends an equality-delete file for the given ids.
func CommitEqualityDeleteOnTable(ctx context.Context, cfg Config, tableRef string, ids []int64) error {
	tbl, err := loadTable(ctx, cfg, tableRef)
	if err != nil {
		return err
	}
	_, err = commitEqualityDelete(ctx, tbl, ids)
	return err
}

// ParseInt64List parses comma-separated integers.
func ParseInt64List(raw string) ([]int64, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil, fmt.Errorf("empty id list")
	}
	parts := strings.Split(raw, ",")
	out := make([]int64, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		value, err := strconv.ParseInt(part, 10, 64)
		if err != nil {
			return nil, fmt.Errorf("parse id %q: %w", part, err)
		}
		out = append(out, value)
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("empty id list")
	}
	return out, nil
}

// ReloadTable returns a fresh table handle after concurrent writes.
func ReloadTable(ctx context.Context, cfg Config, tableRef string) (*table.Table, error) {
	return loadTable(ctx, cfg, tableRef)
}
