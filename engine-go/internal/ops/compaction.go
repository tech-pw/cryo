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
	"github.com/cryo-lake/cryo/engine-go/internal/testhook"
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

	if err := validateCompactionSortMode(tbl, op.Params); err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, err)
	}

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

	morCompaction := validate.HasDeleteFilesInScope(tasks)
	cfg, partialProgress := compactionConfigFromParams(op.Params)
	if morCompaction {
		cfg = compactionConfigForMORParams(cfg, op.Params)
	}

	if tbl.CurrentSnapshot() == nil && len(tasks) == 0 {
		progress.Completed(operation, 0, 0)
		return rewriteResult(true, 0, 0, 0, 0, 0, "")
	}

	if err := validate.AssertPartitionedDeletionVectorsUnsupported(tbl, tasks); err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, err)
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

	scopedTasks := collectPlanTasks(plan)
	rewrittenPaths := rewrittenPathsFromPlan(plan)
	if err := validate.AssertDeleteFileInvariant(rewrittenPaths, scopedTasks); err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, err)
	}

	// Memory/file caps may split bin-pack groups; re-apply min_input_files so we
	// never execute Spark-illegal 1→1 rewrites after a tight max_bytes_per_group.
	groups := finalizeRewriteGroups(
		toTaskGroups(plan.Groups),
		groupSplitLimitsFromParams(op.Params),
		int(cfg.MinInputFiles),
	)
	if len(groups) == 0 {
		progress.Completed(operation, 0, 0)
		return rewriteResult(true, 0, 0, 0, 0, 0, "")
	}

	// Partial-progress: commit each group as its own atomic snapshot with per-group
	// commit-race resilience and durable checkpoints. When group_concurrency > 1,
	// groups are staged in parallel (memory-budgeted) then committed serially.
	if partialProgress {
		result, runErr := executePartialProgressParallel(
			ctx, req, tbl, op, cfg, rowFilter, morCompaction, groups, rewrittenPaths,
		)
		if runErr != nil {
			return failRewrite(operation, runErr)
		}
		progress.Completed(operation, inputFiles, result.bytesWritten)
		return rewriteResult(true, inputFiles, result.outputFiles, inputFiles, result.bytesRemoved, result.bytesWritten, "")
	}

	// Single all-or-nothing commit. Reload + re-run on a lost optimistic-concurrency
	// race (PDAE-8353): Hive/Glue often surface "branch \"main\" has changed" without
	// wrapping ErrCommitFailed, and COW tables hit the same race as MOR. iceberg-go
	// also retries inside doCommit; this outer loop re-plans from the current snapshot
	// after a concurrent writer lands.
	attempts := rewriteCommitAttempts()

	var lastErr error
	for attempt := range attempts {
		if attempt > 0 {
			reloaded, reloadErr := reloadTable(ctx, req)
			if reloadErr != nil {
				progress.Failed(operation, reloadErr.Error())
				return failResult(operation, reloadErr)
			}
			tbl = reloaded
		}

		result, runErr := executeRewriteOnce(ctx, tbl, op, cfg, rowFilter, groups, rewrittenPaths, morCompaction)
		if runErr == nil {
			progress.Completed(operation, inputFiles, result.bytesWritten)
			return rewriteResult(
				true,
				inputFiles,
				result.outputFiles,
				inputFiles,
				result.bytesRemoved,
				result.bytesWritten,
				"",
			)
		}
		lastErr = runErr
		if !isRetryableCommitError(runErr, isRESTCatalog(req)) || attempt == attempts-1 {
			return failRewrite(operation, runErr)
		}
	}

	return failRewrite(operation, lastErr)
}

// failRewrite emits a failed progress event and a rewrite_data_files OperationResult,
// tagging commit-race losses so the caller can classify them (commitRaceLostToken).
func failRewrite(operation string, err error) protocol.OperationResult {
	msg := rewriteErrorMessage(err)
	progress.Failed(operation, msg)
	return rewriteResult(false, 0, 0, 0, 0, 0, msg)
}

type rewriteOnceResult struct {
	outputFiles  uint64
	bytesRemoved uint64
	bytesWritten uint64
}

func executeRewriteOnce(
	ctx context.Context,
	tbl *table.Table,
	op *protocol.OperationRequest,
	cfg compaction.Config,
	rowFilter iceberg.BooleanExpression,
	groups []table.CompactionTaskGroup,
	rewrittenPaths map[string]struct{},
	morCompaction bool,
) (rewriteOnceResult, error) {
	var zero rewriteOnceResult

	beforeRows, err := countScopedRows(ctx, tbl, rowFilter, op.Predicates)
	if err != nil {
		return zero, err
	}

	beforeMeta := validate.CaptureMetadataSnapshot(tbl)
	snapshotProps := validate.CustomSnapshotProps(tbl)

	ioProps := rewriteIOProperties(op.Params)
	stageTbl, cloneErr := cloneTableWithProperties(tbl, ioProps)
	if cloneErr != nil {
		return zero, fmt.Errorf("clone table for rewrite IO props: %w", cloneErr)
	}

	txn := tbl.NewTransaction()
	if err := applyCompactionTestProperties(txn); err != nil {
		return zero, fmt.Errorf("configure compaction test properties: %w", err)
	}
	if morCompaction {
		if err := ensureMORCompactionProperties(txn, tbl.Metadata().Properties()); err != nil {
			return zero, fmt.Errorf("configure MOR compaction properties: %w", err)
		}
	}

	groupOpts := rewriteGroupOptions(cfg.TargetFileSizeBytes, op.Params)
	rewrite := txn.NewRewrite(snapshotProps)
	var bytesBefore, bytesAfter int64
	var addedFiles, removedFiles int

	for i, group := range groups {
		if err := ctx.Err(); err != nil {
			return zero, err
		}
		if len(group.Tasks) == 0 {
			continue
		}
		progress.Phase("rewrite_data_files", "read_write_started", i, len(groups),
			uint64(len(group.Tasks)), uint64(groupCompressedBytes(group)))
		gr, execErr := table.ExecuteCompactionGroup(ctx, stageTbl, group, groupOpts...)
		if execErr != nil {
			return zero, fmt.Errorf("rewrite_data_files: %w", execErr)
		}
		if len(gr.OldDataFiles) == 0 && len(gr.NewDataFiles) == 0 {
			continue
		}
		progress.Phase("rewrite_data_files", "read_write_done", i, len(groups),
			uint64(len(gr.NewDataFiles)), uint64(gr.BytesAfter))
		rewrite.ApplyResult(gr)
		bytesBefore += gr.BytesBefore
		bytesAfter += gr.BytesAfter
		addedFiles += len(gr.NewDataFiles)
		removedFiles += len(gr.OldDataFiles)
	}

	if addedFiles == 0 && removedFiles == 0 {
		return zero, nil
	}

	if morCompaction {
		deadEqDeletes, eqErr := collectDeadEqualityDeletes(ctx, tbl, rewrittenPaths)
		if eqErr != nil {
			return zero, eqErr
		}
		for _, df := range deadEqDeletes {
			rewrite.DeleteFile(df)
		}
	}

	if err := rewrite.Commit(ctx); err != nil {
		return zero, fmt.Errorf("rewrite_data_files: %w", err)
	}

	afterRows, err := countScopedRowsOnTxn(ctx, txn, tbl, rowFilter, op.Predicates)
	if err != nil {
		return zero, err
	}
	if err := validate.AssertRowCountInvariant(beforeRows, afterRows); err != nil {
		return zero, err
	}

	testhook.BeforeCommit()

	updated, err := txn.Commit(ctx)
	if err != nil {
		return zero, fmt.Errorf("commit rewrite_data_files: %w", err)
	}

	if err := validate.AssertMetadataStable(beforeMeta, updated); err != nil {
		return zero, err
	}

	return rewriteOnceResult{
		outputFiles:  uint64(addedFiles),
		bytesRemoved: uint64(bytesBefore),
		bytesWritten: uint64(bytesAfter),
	}, nil
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

func compactionConfigFromParams(raw json.RawMessage) (compaction.Config, bool) {
	cfg := compaction.DefaultConfig()
	target := int64Param(raw, "target_file_size_bytes", defaultTargetFileSizeBytes)
	minSize := int64Param(raw, "min_file_size_bytes", defaultMinFileSizeBytes)
	cfg.TargetFileSizeBytes = target
	cfg.MinFileSizeBytes = minSize
	if cfg.MaxFileSizeBytes <= cfg.MinFileSizeBytes {
		cfg.MaxFileSizeBytes = target * 9 / 5
	}
	// Always set MinInputFiles (Spark rewrite_data_files / Iceberg semantics).
	// Default 2: pack at least two undersized files toward target_file_size_bytes.
	cfg.MinInputFiles = uint(minInputFilesFromParams(raw))
	partialProgress := boolParam(raw, "partial_progress", false)
	return cfg, partialProgress
}

func compactionConfigForMORParams(cfg compaction.Config, raw json.RawMessage) compaction.Config {
	cfg.DeleteFileThreshold = 1
	if cfg.MinInputFiles > 1 {
		cfg.MinInputFiles = 1
	}
	return cfg
}

func applyCompactionTestProperties(txn *table.Transaction) error {
	retries := testhook.CommitNumRetriesOverride()
	if retries == "" {
		return nil
	}
	return txn.SetProperties(iceberg.Properties{
		table.CommitNumRetriesKey:          retries,
		table.CommitMinRetryWaitMsKey:      "10",
		table.CommitMaxRetryWaitMsKey:      "100",
		table.CommitTotalRetryTimeoutMsKey: "5000",
	})
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
	if validate.HasDeleteFilesInScope(tasks) {
		return countLiveTaskRows(ctx, tbl, tasks)
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
		if validate.HasDeleteFilesInScope(tasks) {
			return countLiveTaskRowsOnScan(ctx, scan, tasks)
		}
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
	if validate.HasDeleteFilesInScope(filtered) {
		return countLiveTaskRowsOnScan(ctx, scan, filtered)
	}
	return countTaskRows(filtered)
}

func countLiveTaskRows(ctx context.Context, tbl *table.Table, tasks []table.FileScanTask) (int64, error) {
	scan := tbl.Scan()
	return countLiveTaskRowsOnScan(ctx, scan, tasks)
}

func countLiveTaskRowsOnScan(
	ctx context.Context,
	scan *table.Scan,
	tasks []table.FileScanTask,
) (int64, error) {
	if len(tasks) == 0 {
		return 0, nil
	}
	_, records, err := scan.ReadTasks(ctx, tasks)
	if err != nil {
		return 0, fmt.Errorf("read scoped tasks for live row count: %w", err)
	}
	var total int64
	for rec, err := range records {
		if err != nil {
			return 0, err
		}
		total += rec.NumRows()
		rec.Release()
	}
	return total, nil
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
