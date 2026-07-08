package ops

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/table"
	"github.com/apache/iceberg-go/table/compaction"

	"github.com/cryo-lake/cryo/engine-go/internal/catalog"
	"github.com/cryo-lake/cryo/engine-go/internal/predicate"
	"github.com/cryo-lake/cryo/engine-go/internal/progress"
	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
	"github.com/cryo-lake/cryo/engine-go/internal/validate"
)

const (
	defaultTargetFileSizeBytes = 268_435_456 // 256 MB
	defaultMinFileSizeBytes    = 104_857_600 // 100 MB
)

func RewriteDataFiles(ctx context.Context, req *protocol.ExecuteRequest, op *protocol.OperationRequest) protocol.OperationResult {
	const operation = "rewrite_data_files"
	progress.Started(operation)

	if isExecuteMode(req.Mode) {
		if reason := predicate.UnsupportedReason(op); reason != "" {
			progress.Completed(operation, 0, 0)
			return rewriteResult(false, 0, 0, 0, 0, 0, reason)
		}
	}

	_, tbl, err := catalog.LoadTable(ctx, req)
	if err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, err)
	}

	cfg := compactionConfigFromParams(op.Params)
	rowFilter, err := predicate.BuildRowFilter(tbl.Schema(), op.Predicates)
	if err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, err)
	}

	tasks, err := planScopedTasks(ctx, tbl, rowFilter, op.Predicates)
	if err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, err)
	}

	if tbl.CurrentSnapshot() == nil && len(tasks) == 0 {
		progress.Completed(operation, 0, 0)
		return rewriteResult(true, 0, 0, 0, 0, 0, "")
	}

	plan, err := cfg.PlanCompaction(tasks)
	if err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, fmt.Errorf("plan compaction: %w", err))
	}

	candidateFiles, candidateBytes := compactionCandidates(plan)
	inputFiles := uint64(candidateFiles)
	outputFiles := uint64(plan.EstOutputFiles)

	progress.Progress(operation, 50, inputFiles, candidateBytes)

	if !isExecuteMode(req.Mode) {
		progress.Completed(operation, inputFiles, candidateBytes)
		return rewriteResult(true, inputFiles, outputFiles, inputFiles, candidateBytes, 0, "")
	}

	if candidateFiles == 0 {
		progress.Completed(operation, 0, 0)
		return rewriteResult(true, 0, 0, 0, 0, 0, "")
	}

	if err := validate.AssertNoDeleteFilesInScope(collectPlanTasks(plan)); err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, err)
	}

	beforeRows, err := countScopedRows(ctx, tbl, rowFilter, op.Predicates)
	if err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, err)
	}

	beforeMeta := validate.CaptureMetadataSnapshot(tbl)
	groups := toTaskGroups(plan.Groups)
	snapshotProps := validate.CustomSnapshotProps(tbl)

	txn := tbl.NewTransaction()
	rewriteOpts := table.RewriteDataFilesOptions{
		SnapshotProps: snapshotProps,
		GroupOptions: []table.CompactionGroupOption{
			table.WithCompactionTargetFileSize(cfg.TargetFileSizeBytes),
		},
	}

	result, err := txn.RewriteDataFiles(ctx, groups, rewriteOpts)
	if err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, fmt.Errorf("rewrite_data_files: %w", err))
	}

	if result == nil || result.RewrittenGroups == 0 {
		progress.Completed(operation, 0, 0)
		return rewriteResult(true, 0, 0, 0, 0, 0, "")
	}

	afterRows, err := countScopedRowsOnTxn(ctx, txn, tbl, rowFilter, op.Predicates)
	if err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, err)
	}
	if err := validate.AssertRowCountInvariant(beforeRows, afterRows); err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, err)
	}

	updated, err := txn.Commit(ctx)
	if err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, fmt.Errorf("commit rewrite_data_files: %w", err))
	}

	if err := validate.AssertMetadataStable(beforeMeta, updated); err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, err)
	}

	bytesWritten := uint64(result.BytesAfter)
	bytesRemoved := uint64(result.BytesBefore)
	progress.Completed(operation, inputFiles, bytesWritten)

	return rewriteResult(
		true,
		inputFiles,
		uint64(result.AddedDataFiles),
		inputFiles,
		bytesRemoved,
		bytesWritten,
		"",
	)
}

func planScopedTasks(
	ctx context.Context,
	tbl *table.Table,
	rowFilter iceberg.BooleanExpression,
	predicates []protocol.PredicateRequest,
) ([]table.FileScanTask, error) {
	scan := tbl.Scan(table.WithRowFilter(rowFilter))
	tasks, err := scan.PlanFiles(ctx)
	if err != nil {
		return nil, fmt.Errorf("plan scoped files: %w", err)
	}
	if !predicate.HasPredicates(&protocol.OperationRequest{Predicates: predicates}) {
		return tasks, nil
	}

	filtered := make([]table.FileScanTask, 0, len(tasks))
	for _, task := range tasks {
		match, err := predicate.MatchesAnyDataFile(tbl, task.File, predicates)
		if err != nil {
			return nil, err
		}
		if match {
			filtered = append(filtered, task)
		}
	}
	return filtered, nil
}

func compactionConfigFromParams(raw json.RawMessage) compaction.Config {
	cfg := compaction.DefaultConfig()
	target := int64Param(raw, "target_file_size_bytes", defaultTargetFileSizeBytes)
	minSize := int64Param(raw, "min_file_size_bytes", defaultMinFileSizeBytes)
	cfg.TargetFileSizeBytes = target
	cfg.MinFileSizeBytes = minSize
	if cfg.MaxFileSizeBytes <= cfg.MinFileSizeBytes {
		cfg.MaxFileSizeBytes = target * 9 / 5
	}
	if minInput := intParam(raw, "min_input_files", 0); minInput > 0 {
		cfg.MinInputFiles = uint(minInput)
	}
	return cfg
}

func compactionCandidates(plan compaction.Plan) (files uint64, bytes uint64) {
	for _, group := range plan.Groups {
		files += uint64(len(group.Tasks))
		bytes += uint64(group.TotalSizeBytes)
	}
	return files, bytes
}

func collectPlanTasks(plan compaction.Plan) []table.FileScanTask {
	tasks := make([]table.FileScanTask, 0)
	for _, group := range plan.Groups {
		tasks = append(tasks, group.Tasks...)
	}
	return tasks
}

func toTaskGroups(groups []compaction.Group) []table.CompactionTaskGroup {
	out := make([]table.CompactionTaskGroup, len(groups))
	for i, group := range groups {
		out[i] = table.CompactionTaskGroup{
			PartitionKey:   group.PartitionKey,
			Tasks:          group.Tasks,
			TotalSizeBytes: group.TotalSizeBytes,
		}
	}
	return out
}

func countScopedRows(
	ctx context.Context,
	tbl *table.Table,
	rowFilter iceberg.BooleanExpression,
	predicates []protocol.PredicateRequest,
) (int64, error) {
	tasks, err := planScopedTasks(ctx, tbl, rowFilter, predicates)
	if err != nil {
		return 0, err
	}
	return countTaskRows(tasks)
}

func countScopedRowsOnTxn(
	ctx context.Context,
	txn *table.Transaction,
	tbl *table.Table,
	rowFilter iceberg.BooleanExpression,
	predicates []protocol.PredicateRequest,
) (int64, error) {
	scan, err := txn.Scan(table.WithRowFilter(rowFilter))
	if err != nil {
		return 0, fmt.Errorf("scan staged table: %w", err)
	}
	tasks, err := scan.PlanFiles(ctx)
	if err != nil {
		return 0, fmt.Errorf("plan staged scoped files: %w", err)
	}
	if !predicate.HasPredicates(&protocol.OperationRequest{Predicates: predicates}) {
		return countTaskRows(tasks)
	}
	filtered := make([]table.FileScanTask, 0, len(tasks))
	for _, task := range tasks {
		match, err := predicate.MatchesAnyDataFile(tbl, task.File, predicates)
		if err != nil {
			return 0, err
		}
		if match {
			filtered = append(filtered, task)
		}
	}
	return countTaskRows(filtered)
}

func countTaskRows(tasks []table.FileScanTask) (int64, error) {
	var total int64
	for _, task := range tasks {
		total += task.File.Count()
	}
	return total, nil
}

func rewriteResult(
	success bool,
	inputFiles, outputFiles, filesRewritten, bytesRemoved, bytesWritten uint64,
	errMsg string,
) protocol.OperationResult {
	var errPtr *string
	if errMsg != "" {
		errPtr = &errMsg
	}
	return protocol.OperationResult{
		Operation:      "rewrite_data_files",
		Success:        success,
		FilesRewritten: &filesRewritten,
		InputFiles:     &inputFiles,
		OutputFiles:    &outputFiles,
		BytesRemoved:   &bytesRemoved,
		BytesWritten:   &bytesWritten,
		DurationMS:     0,
		Error:          errPtr,
	}
}