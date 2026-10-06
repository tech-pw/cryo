package ops

import (
	"context"
	"fmt"
	"time"

	"github.com/cryo-lake/cryo/engine-go/internal/catalog"
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

// safeExecuteOperation recovers a panic in the underlying iceberg-go scan/rewrite path (seen in
// practice: a malformed timestamptz value in a table's own manifest metadata crashes the parser)
// so one corrupted table can't take the whole subprocess down with it. Without this, a panic in
// ExecuteOperation propagates through Execute's loop to main() and kills the process outright --
// every other operation in the same job (even ones with no relation to the failing code path,
// e.g. expire_snapshots after a panicking optimize) gets reported as "subprocess exited before
// results" instead of actually running.
func safeExecuteOperation(ctx context.Context, req *protocol.ExecuteRequest, op *protocol.OperationRequest) protocol.OperationResult {
	return recoverOperation(op.OperationType, func() protocol.OperationResult {
		return ExecuteOperation(ctx, req, op)
	})
}

// recoverOperation runs fn and converts any panic into a failed OperationResult instead of
// letting it propagate. Factored out from safeExecuteOperation so the recover mechanism itself
// is unit-testable without needing real iceberg-go fixtures to trigger a panic.
func recoverOperation(operationType string, fn func() protocol.OperationResult) (result protocol.OperationResult) {
	started := time.Now()
	defer func() {
		if r := recover(); r != nil {
			msg := fmt.Sprintf("panic in %s: %v", operationType, r)
			result = protocol.OperationResult{
				Operation:  operationType,
				Success:    false,
				Error:      &msg,
				DurationMS: uint64(time.Since(started).Milliseconds()),
			}
		}
	}()
	return fn()
}

// Execute runs all operations in the request and returns the aggregate response.
func Execute(ctx context.Context, req *protocol.ExecuteRequest) protocol.ExecuteResponse {
	// Seed the context with the source's static S3 credentials so the commit write path
	// (which rebuilds FileIO from table properties, not catalog properties) does not fall back
	// to the default AWS chain / STS AssumeRole. See catalog.WithSourceAWSConfig.
	ctx = catalog.WithSourceAWSConfig(ctx, req)

	results := make([]protocol.OperationResult, 0, len(req.Operations))
	allOK := true

	for i := range req.Operations {
		result := safeExecuteOperation(ctx, req, &req.Operations[i])
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
