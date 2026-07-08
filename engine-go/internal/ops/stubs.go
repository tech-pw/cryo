package ops

import (
	"context"
	"fmt"

	"github.com/cryo-lake/cryo/engine-go/internal/catalog"
	"github.com/cryo-lake/cryo/engine-go/internal/progress"
	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
)

func RewriteManifests(ctx context.Context, req *protocol.ExecuteRequest, op *protocol.OperationRequest) protocol.OperationResult {
	const operation = "rewrite_manifests"
	progress.Started(operation)

	if _, _, err := catalog.LoadTable(ctx, req); err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, err)
	}

	var msg *string
	success := true
	if isExecuteMode(req.Mode) {
		text := "rewrite_manifests execute is not available on iceberg-go (not implemented upstream)"
		msg = &text
		success = false
	}
	zero := uint64(0)
	progress.Completed(operation, 0, 0)
	return protocol.OperationResult{
		Operation:          operation,
		Success:            success,
		ManifestsRewritten: &zero,
		Error:              msg,
		DurationMS:         0,
	}
}

func unsupportedOperation(operation string) protocol.OperationResult {
	msg := fmt.Sprintf("unsupported operation type: %s", operation)
	return protocol.OperationResult{
		Operation:  operation,
		Success:    false,
		Error:      &msg,
		DurationMS: 0,
	}
}
