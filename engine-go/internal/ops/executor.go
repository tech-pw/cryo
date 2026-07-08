package ops

import (
	"context"
	"fmt"
	"time"

	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
)

// ExecuteOperation dispatches a single maintenance operation.
func ExecuteOperation(ctx context.Context, req *protocol.ExecuteRequest, op *protocol.OperationRequest) protocol.OperationResult {
	started := time.Now()
	var result protocol.OperationResult

	switch op.OperationType {
	case "optimize", "rewrite_data_files":
		result = RewriteDataFiles(ctx, req, op)
	case "expire_snapshots":
		result = ExpireSnapshots(ctx, req, op)
	case "remove_orphan_files":
		result = RemoveOrphanFiles(ctx, req, op)
	case "optimize_manifests", "rewrite_manifests":
		result = RewriteManifests(ctx, req, op)
	default:
		result = unsupportedOperation(op.OperationType)
	}

	result.DurationMS = uint64(time.Since(started).Milliseconds())
	return result
}

// Execute runs all operations in the request and returns the aggregate response.
func Execute(ctx context.Context, req *protocol.ExecuteRequest) protocol.ExecuteResponse {
	results := make([]protocol.OperationResult, 0, len(req.Operations))
	allOK := true

	for i := range req.Operations {
		result := ExecuteOperation(ctx, req, &req.Operations[i])
		results = append(results, result)
		if !result.Success {
			allOK = false
		}
	}

	return protocol.ExecuteResponse{
		OK:         allOK && allResultsSuccessful(results),
		TableRef:   req.TableRef,
		Operations: len(results),
		Results:    results,
	}
}

func allResultsSuccessful(results []protocol.OperationResult) bool {
	for _, result := range results {
		if !result.Success {
			return false
		}
	}
	return true
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
