package ops

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/table"
	"github.com/apache/iceberg-go/table/compaction"

	"github.com/cryo-lake/cryo/engine-go/internal/catalog"
	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
)

const appCommitRetries = 4

func isRESTCatalog(req *protocol.ExecuteRequest) bool {
	if req == nil {
		return true
	}
	catalogType := strings.TrimSpace(req.CatalogProperties["type"])
	if catalogType == "" {
		return true
	}
	return strings.EqualFold(catalogType, "rest")
}

func ensureMORCompactionProperties(txn *table.Transaction, existing iceberg.Properties) error {
	desired := iceberg.Properties{
		table.WriteDeleteIsolationLevelKey:     string(table.IsolationSerializable),
		table.CommitNumRetriesKey:              "4",
		table.CommitMinRetryWaitMsKey:          "100",
		table.CommitMaxRetryWaitMsKey:          "60000",
		table.CommitTotalRetryTimeoutMsKey:     "1800000",
	}
	toSet := iceberg.Properties{}
	for key, value := range desired {
		if _, ok := existing[key]; !ok {
			toSet[key] = value
		}
	}
	if len(toSet) == 0 {
		return nil
	}
	return txn.SetProperties(toSet)
}

func rewrittenPathsFromPlan(plan compaction.Plan) map[string]struct{} {
	paths := make(map[string]struct{})
	for _, group := range plan.Groups {
		for _, task := range group.Tasks {
			paths[task.File.FilePath()] = struct{}{}
		}
	}
	return paths
}

func rewrittenPathsForGroup(
	rewrittenPaths map[string]struct{},
	group table.CompactionTaskGroup,
) map[string]struct{} {
	out := make(map[string]struct{})
	for _, task := range group.Tasks {
		path := task.File.FilePath()
		if _, ok := rewrittenPaths[path]; ok {
			out[path] = struct{}{}
		}
	}
	return out
}

func collectDeadEqualityDeletesForGroup(
	ctx context.Context,
	tbl *table.Table,
	groupRewritten map[string]struct{},
) ([]iceberg.DataFile, error) {
	return collectDeadEqualityDeletes(ctx, tbl, groupRewritten)
}

func collectDeadEqualityDeletes(
	ctx context.Context,
	tbl *table.Table,
	rewrittenPaths map[string]struct{},
) ([]iceberg.DataFile, error) {
	snap := tbl.CurrentSnapshot()
	if snap == nil || len(rewrittenPaths) == 0 {
		return nil, nil
	}
	fs, err := tbl.FS(ctx)
	if err != nil {
		return nil, fmt.Errorf("open table filesystem for eq-delete cleanup: %w", err)
	}
	return compaction.CollectDeadEqualityDeletes(ctx, fs, snap, rewrittenPaths)
}

// isRetryableCommitError classifies a commit failure as a retryable optimistic-concurrency
// conflict. REST catalogs reliably wrap conflicts with table.ErrCommitFailed/ErrCommitDiverged,
// so errors.Is is precise there. Non-REST catalogs (Hive, Glue, JDBC) don't -- confirmed live,
// 2026-07-21: a genuine "branch \"main\" has changed" conflict against a Hive metastore
// propagated as an immediate hard failure instead of retrying, even on a call site that had
// already budgeted extra attempts specifically for this case (attempts was bumped, but every
// attempt after the first was unreachable because this check always returned false first).
// Since non-REST catalogs give no reliable way to distinguish a conflict from any other
// commit error, treat any commit error as retryable there -- same assumption the single-commit
// MOR path's `attempts` calculation already makes.
func isRetryableCommitError(err error, isREST bool) bool {
	if err == nil {
		return false
	}
	if !isREST {
		return true
	}
	return errors.Is(err, table.ErrCommitFailed) || errors.Is(err, table.ErrCommitDiverged)
}

func reloadTable(ctx context.Context, req *protocol.ExecuteRequest) (*table.Table, error) {
	_, tbl, err := catalog.LoadTable(ctx, req)
	if err != nil {
		return nil, err
	}
	return tbl, nil
}
