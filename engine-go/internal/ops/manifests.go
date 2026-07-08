package ops

import (
	"context"
	"fmt"

	"github.com/apache/iceberg-go/table"

	"github.com/cryo-lake/cryo/engine-go/internal/catalog"
	"github.com/cryo-lake/cryo/engine-go/internal/progress"
	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
)

const manifestExecuteUnavailableMsg = "rewrite_manifests execute is not available on iceberg-go " +
	"(not implemented upstream); route optimize_manifests to Trino/Spark"

func RewriteManifests(ctx context.Context, req *protocol.ExecuteRequest, op *protocol.OperationRequest) protocol.OperationResult {
	const operation = "rewrite_manifests"
	progress.Started(operation)

	_, tbl, err := catalog.LoadTable(ctx, req)
	if err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, err)
	}

	manifestCount, err := countCurrentSnapshotManifests(ctx, tbl)
	if err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, err)
	}

	if isExecuteMode(req.Mode) {
		msg := manifestExecuteUnavailableMsg
		progress.Completed(operation, manifestCount, 0)
		return manifestResult(false, manifestCount, &msg)
	}

	progress.Progress(operation, 100, manifestCount, 0)
	progress.Completed(operation, manifestCount, 0)
	return manifestResult(true, manifestCount, nil)
}

func countCurrentSnapshotManifests(ctx context.Context, tbl *table.Table) (uint64, error) {
	snap := tbl.CurrentSnapshot()
	if snap == nil {
		return 0, nil
	}
	fs, err := tbl.FS(ctx)
	if err != nil {
		return 0, fmt.Errorf("open table filesystem for manifest analyze: %w", err)
	}
	manifests, err := snap.Manifests(fs)
	if err != nil {
		return 0, fmt.Errorf("load current snapshot manifests: %w", err)
	}
	return uint64(len(manifests)), nil
}

func manifestResult(success bool, manifestCount uint64, errMsg *string) protocol.OperationResult {
	return protocol.OperationResult{
		Operation:          "rewrite_manifests",
		Success:            success,
		InputFiles:         &manifestCount,
		OutputFiles:        &manifestCount,
		ManifestsRewritten: &manifestCount,
		Error:              errMsg,
		DurationMS:         0,
	}
}
