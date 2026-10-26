package ops

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/apache/iceberg-go/table"

	"github.com/cryo-lake/cryo/engine-go/internal/catalog"
	"github.com/cryo-lake/cryo/engine-go/internal/progress"
	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
)

func ExpireSnapshots(ctx context.Context, req *protocol.ExecuteRequest, op *protocol.OperationRequest) protocol.OperationResult {
	const operation = "expire_snapshots"
	progress.Started(operation)

	cat, tbl, err := catalog.LoadTable(ctx, req)
	if err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, err)
	}
	_ = cat

	retentionHours := intParam(op.Params, "retention_hours", 0)
	if retentionHours <= 0 {
		retentionHours = intParam(op.Params, "retention_days", 0) * 24
	}
	retainLast := intParam(op.Params, "retain_last", 0)

	var expired uint64
	if isExecuteMode(req.Mode) {
		updated, count, err := commitExpireSnapshots(ctx, req, tbl, retentionHours, retainLast)
		if err != nil {
			progress.Failed(operation, err.Error())
			return failResult(operation, fmt.Errorf("failed to commit expire_snapshots: %w", err))
		}
		expired = count
		_ = updated
	} else {
		expired = estimateExpiredSnapshots(tbl, retentionHours, retainLast)
	}

	progress.Progress(operation, 100, expired, 0)
	progress.Completed(operation, expired, 0)
	return protocol.OperationResult{
		Operation:        operation,
		Success:          true,
		SnapshotsExpired: &expired,
		DurationMS:       0,
	}
}

func snapshotIDs(tbl *table.Table) map[int64]struct{} {
	ids := make(map[int64]struct{})
	for _, snap := range tbl.Metadata().Snapshots() {
		ids[snap.SnapshotID] = struct{}{}
	}
	return ids
}

func countRemovedSnapshots(before, after map[int64]struct{}) uint64 {
	var removed uint64
	for id := range before {
		if _, ok := after[id]; !ok {
			removed++
		}
	}
	return removed
}

func estimateExpiredSnapshots(tbl *table.Table, retentionHours, retainLast int) uint64 {
	before := len(snapshotIDs(tbl))
	if before == 0 {
		return 0
	}
	after := before
	if retainLast > 0 && before > retainLast {
		after = retainLast
	}
	if retentionHours > 0 {
		cutoff := time.Now().Add(-time.Duration(retentionHours) * time.Hour).UnixMilli()
		kept := 0
		for _, snap := range tbl.Metadata().Snapshots() {
			if snap.TimestampMs >= cutoff {
				kept++
			}
		}
		if retainLast > 0 && kept < retainLast {
			kept = retainLast
		}
		if kept < after {
			after = kept
		}
	}
	if after > before {
		after = before
	}
	return uint64(before - after)
}

func expireOptions(retentionHours, retainLast int) []table.ExpireSnapshotsOpt {
	opts := []table.ExpireSnapshotsOpt{table.WithPostCommit(false)}
	if retentionHours > 0 {
		opts = append(opts, table.WithOlderThan(time.Duration(retentionHours)*time.Hour))
	} else if retentionHours == 0 && retainLast > 0 {
		opts = append(opts, table.WithOlderThan(0))
	}
	if retainLast > 0 {
		opts = append(opts, table.WithRetainLast(retainLast))
	}
	return opts
}

// _expireCommitMaxRetries and the backoff schedule are deliberately larger/slower than
// appCommitRetries (used elsewhere for rewrite commit races). Confirmed live, 2026-07-22:
// a real streaming table's Hive metastore lock stayed contended across all 4 of
// appCommitRetries' immediate, no-backoff retries -- expire_snapshots exhausted its budget
// and failed outright. A live writer committing every few seconds needs more attempts AND
// deliberate spacing between them (immediate retries just re-lose the same race), not just
// a bigger number fired back-to-back.
const _expireCommitMaxRetries = 12

var _expireCommitBackoff = [...]time.Duration{
	250 * time.Millisecond,
	500 * time.Millisecond,
	1 * time.Second,
	2 * time.Second,
	4 * time.Second,
	8 * time.Second, // remaining attempts repeat this cap
}

func expireCommitBackoffFor(attempt int) time.Duration {
	if attempt < 0 {
		attempt = 0
	}
	if attempt >= len(_expireCommitBackoff) {
		attempt = len(_expireCommitBackoff) - 1
	}
	return _expireCommitBackoff[attempt]
}

func commitExpireSnapshots(
	ctx context.Context,
	req *protocol.ExecuteRequest,
	tbl *table.Table,
	retentionHours, retainLast int,
) (*table.Table, uint64, error) {
	current := tbl
	var totalExpired uint64
	// Bare transient-error retry (dropped Hive Thrift connection, lock contention against a
	// live writer, etc.) -- confirmed live, 2026-07-21/22: an "EOF" from a Hive metastore, and
	// separately a "failed to acquire lock" against an actively-written streaming table, both
	// aborted expire_snapshots outright. Distinct from the rewrite path's isRetryableCommitError
	// classification (that's about telling an optimistic-concurrency conflict apart from other
	// errors on catalogs that don't wrap it); here any commit error is worth reloading,
	// backing off, and re-attempting -- bounded so a permanently broken connection or a table
	// under truly continuous write pressure still fails instead of retrying forever.
	attempt := 0

	for {
		beforeCount := len(snapshotIDs(current))
		if retainLast > 0 && beforeCount <= retainLast {
			return current, totalExpired, nil
		}

		opts := expireOptions(retentionHours, retainLast)
		if retentionHours == 0 && retainLast > 0 && beforeCount > retainLast+1 {
			// REST catalog rejects multi-snapshot removal in a single commit; expire one step at a time.
			opts = expireOptions(0, beforeCount-1)
		}

		txn := current.NewTransaction()
		if err := txn.ExpireSnapshots(opts...); err != nil {
			return current, totalExpired, err
		}
		updated, err := txn.Commit(ctx)
		if err != nil {
			if attempt >= _expireCommitMaxRetries {
				return current, totalExpired, err
			}
			select {
			case <-ctx.Done():
				return current, totalExpired, ctx.Err()
			case <-time.After(expireCommitBackoffFor(attempt)):
			}
			attempt++
			reloaded, reloadErr := reloadTable(ctx, req)
			if reloadErr != nil {
				return current, totalExpired, err
			}
			current = reloaded
			continue
		}
		removed := countRemovedSnapshots(snapshotIDs(current), snapshotIDs(updated))
		totalExpired += removed
		if removed == 0 {
			return updated, totalExpired, nil
		}
		current = updated
	}
}

func intParam(raw json.RawMessage, key string, defaultValue int) int {
	if len(raw) == 0 {
		return defaultValue
	}
	var params map[string]json.RawMessage
	if err := json.Unmarshal(raw, &params); err != nil {
		return defaultValue
	}
	value, ok := params[key]
	if !ok {
		return defaultValue
	}
	var asInt int
	if err := json.Unmarshal(value, &asInt); err == nil {
		return asInt
	}
	var asFloat float64
	if err := json.Unmarshal(value, &asFloat); err == nil {
		return int(asFloat)
	}
	return defaultValue
}

func boolParam(raw json.RawMessage, key string, defaultValue bool) bool {
	if len(raw) == 0 {
		return defaultValue
	}
	var params map[string]json.RawMessage
	if err := json.Unmarshal(raw, &params); err != nil {
		return defaultValue
	}
	value, ok := params[key]
	if !ok {
		return defaultValue
	}
	var asBool bool
	if err := json.Unmarshal(value, &asBool); err == nil {
		return asBool
	}
	return defaultValue
}

func failResult(operation string, err error) protocol.OperationResult {
	msg := err.Error()
	return protocol.OperationResult{
		Operation:  operation,
		Success:    false,
		Error:      &msg,
		DurationMS: 0,
	}
}
