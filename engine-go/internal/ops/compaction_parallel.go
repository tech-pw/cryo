package ops

import (
	"context"
	"fmt"
	"runtime"
	"sync"

	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/table"
	"github.com/apache/iceberg-go/table/compaction"

	"github.com/cryo-lake/cryo/engine-go/internal/progress"
	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
	"github.com/cryo-lake/cryo/engine-go/internal/testhook"
	"github.com/cryo-lake/cryo/engine-go/internal/validate"
)

// stagedGroup holds the output of a parallel ExecuteCompactionGroup call,
// ready for a serialized commit on the leader transaction.
type stagedGroup struct {
	index  int
	group  table.CompactionTaskGroup
	result table.CompactionGroupResult
	err    error
}

// executePartialProgressParallel stages groups concurrently (memory-budgeted)
// then commits each staged result serially. Falls back to the serial path when
// group_concurrency <= 1.
func executePartialProgressParallel(
	ctx context.Context,
	req *protocol.ExecuteRequest,
	tbl *table.Table,
	op *protocol.OperationRequest,
	cfg compaction.Config,
	rowFilter iceberg.BooleanExpression,
	morCompaction bool,
	groups []table.CompactionTaskGroup,
	rewrittenPaths map[string]struct{},
) (rewriteOnceResult, error) {
	concurrency := groupConcurrencyFromParams(op.Params)
	if concurrency <= 1 || len(groups) <= 1 {
		return executePartialProgressResumable(
			ctx, req, tbl, op, cfg, rowFilter, morCompaction, groups, rewrittenPaths,
		)
	}
	return runParallelPartialProgress(
		ctx, req, tbl, op, cfg, rowFilter, morCompaction, groups, rewrittenPaths, concurrency,
	)
}

func runParallelPartialProgress(
	ctx context.Context,
	req *protocol.ExecuteRequest,
	tbl *table.Table,
	op *protocol.OperationRequest,
	cfg compaction.Config,
	rowFilter iceberg.BooleanExpression,
	morCompaction bool,
	groups []table.CompactionTaskGroup,
	rewrittenPaths map[string]struct{},
	concurrency int,
) (rewriteOnceResult, error) {
	const operation = "rewrite_data_files"
	var totals rewriteOnceResult

	beforeMeta := validate.CaptureMetadataSnapshot(tbl)
	snapshotProps := validate.CustomSnapshotProps(tbl)
	groupOpts := rewriteGroupOptions(cfg.TargetFileSizeBytes, op.Params)
	budget := memoryBudgetFromParams(op.Params)
	expansion := memoryExpansionFromParams(op.Params)
	limits := groupSplitLimitsFromParams(op.Params)

	ioProps := rewriteIOProperties(op.Params)
	stageTbl, err := cloneTableWithProperties(tbl, ioProps)
	if err != nil {
		return totals, fmt.Errorf("clone table for rewrite IO props: %w", err)
	}

	maxRounds := appCommitRetries + 1
	committedGroups := 0
	var lastConflict error

	for round := range maxRounds {
		if round > 0 {
			reloaded, reloadErr := reloadTable(ctx, req)
			if reloadErr != nil {
				return totals, fmt.Errorf("reload table before parallel round %d: %w", round, reloadErr)
			}
			tbl = reloaded
			stageTbl, err = cloneTableWithProperties(tbl, ioProps)
			if err != nil {
				return totals, err
			}
			roundCfg := cfg
			if morCompaction {
				roundCfg = compactionConfigForMORParams(roundCfg, op.Params)
			}
			replanned, replannedPaths, planErr := planCheckpointGroups(
				ctx, tbl, roundCfg, rowFilter, op.Predicates, limits,
			)
			if planErr != nil {
				return totals, planErr
			}
			tasks := collectGroupTasks(replanned)
			if err := validate.AssertPartitionedDeletionVectorsUnsupported(tbl, tasks); err != nil {
				return totals, err
			}
			if err := validate.AssertDeleteFileInvariant(replannedPaths, tasks); err != nil {
				return totals, err
			}
			groups = replanned
			rewrittenPaths = replannedPaths
			snapshotProps = validate.CustomSnapshotProps(tbl)
			beforeMeta = validate.CaptureMetadataSnapshot(tbl)
			groupOpts = rewriteGroupOptions(roundCfg.TargetFileSizeBytes, op.Params)
		}

		if len(groups) == 0 {
			return totals, nil
		}

		staged, stageErr := stageGroupsParallel(
			ctx, stageTbl, groups, groupOpts, concurrency, budget, expansion, limits, operation,
		)
		if stageErr != nil {
			return totals, stageErr
		}

		conflicted := false
		for _, sg := range staged {
			if sg.err != nil {
				return totals, sg.err
			}
			if len(sg.result.OldDataFiles) == 0 && len(sg.result.NewDataFiles) == 0 {
				continue
			}
			outcome, commitErr := commitStagedGroup(
				ctx, tbl, sg, morCompaction, rewrittenPaths, snapshotProps, beforeMeta,
			)
			if commitErr != nil {
				if isRetryableCommitError(commitErr, isRESTCatalog(req)) {
					lastConflict = commitErr
					conflicted = true
					break
				}
				return totals, commitErr
			}
			if outcome.committed {
				committedGroups++
				totals.add(outcome)
				progress.Checkpoint(operation, committedGroups, len(groups), totals.outputFiles, totals.bytesWritten)
			}
			progress.Progress(operation, uint8((sg.index+1)*100/len(groups)), totals.outputFiles, totals.bytesWritten)
			runtime.GC()
			if err := tbl.Refresh(ctx); err != nil {
				return totals, fmt.Errorf("refresh table after parallel group %d: %w", sg.index, err)
			}
			stageTbl, err = cloneTableWithProperties(tbl, ioProps)
			if err != nil {
				return totals, err
			}
		}

		if !conflicted {
			return totals, nil
		}
	}

	return totals, fmt.Errorf("%w: rewrite_data_files exhausted %d parallel checkpoint rounds: %w",
		errCommitRaceLost, maxRounds, lastConflict)
}

// memoryGate admits groups under a concurrency + estimated-RSS budget.
type memoryGate struct {
	mu        sync.Mutex
	cond      *sync.Cond
	budget    int64
	inflight  int64
	active    int
	maxActive int
	closed    bool
}

func newMemoryGate(budget int64, maxActive int) *memoryGate {
	g := &memoryGate{budget: budget, maxActive: maxActive}
	g.cond = sync.NewCond(&g.mu)
	return g
}

func (g *memoryGate) acquire(ctx context.Context, footprint int64) error {
	g.mu.Lock()
	defer g.mu.Unlock()
	for {
		if g.closed {
			return fmt.Errorf("memory gate closed")
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		// Oversized groups (footprint > budget) run alone when nothing is in flight.
		fits := g.inflight == 0 || g.inflight+footprint <= g.budget
		slots := g.active < g.maxActive
		if fits && slots {
			g.inflight += footprint
			g.active++
			return nil
		}
		g.cond.Wait()
	}
}

func (g *memoryGate) release(footprint int64) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.inflight -= footprint
	if g.inflight < 0 {
		g.inflight = 0
	}
	g.active--
	if g.active < 0 {
		g.active = 0
	}
	g.cond.Broadcast()
}

func (g *memoryGate) close() {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.closed = true
	g.cond.Broadcast()
}

func stageGroupsParallel(
	ctx context.Context,
	stageTbl *table.Table,
	groups []table.CompactionTaskGroup,
	groupOpts []table.CompactionGroupOption,
	concurrency int,
	budget int64,
	expansion int,
	limits groupSplitLimits,
	operation string,
) ([]stagedGroup, error) {
	if concurrency < 1 {
		concurrency = 1
	}
	type job struct {
		index     int
		group     table.CompactionTaskGroup
		footprint int64
	}
	jobs := make(chan job)
	results := make([]stagedGroup, len(groups))
	gate := newMemoryGate(budget, concurrency)
	var wg sync.WaitGroup
	var errMu sync.Mutex
	var firstErr error

	setErr := func(err error) {
		if err == nil {
			return
		}
		errMu.Lock()
		if firstErr == nil {
			firstErr = err
			gate.close()
		}
		errMu.Unlock()
	}

	worker := func() {
		defer wg.Done()
		for j := range jobs {
			progress.GroupStarted(
				operation,
				j.group.PartitionKey,
				len(j.group.Tasks),
				uint64(groupCompressedBytes(j.group)),
				limits.maxFiles,
				uint64(limits.maxBytes),
				j.index,
				len(groups),
			)
			progress.Phase(operation, "read_write_started", j.index, len(groups),
				uint64(len(j.group.Tasks)), uint64(groupCompressedBytes(j.group)))

			gr, err := table.ExecuteCompactionGroup(ctx, stageTbl, j.group, groupOpts...)
			results[j.index] = stagedGroup{index: j.index, group: j.group, result: gr, err: err}
			if err != nil {
				setErr(fmt.Errorf("stage group %d: %w", j.index, err))
			} else {
				progress.Phase(operation, "read_write_done", j.index, len(groups),
					uint64(len(gr.NewDataFiles)), uint64(gr.BytesAfter))
			}
			gate.release(j.footprint)
		}
	}

	for i := 0; i < concurrency; i++ {
		wg.Add(1)
		go worker()
	}

	go func() {
		defer close(jobs)
		for i, group := range groups {
			footprint := estimateGroupFootprint(group, expansion)
			if err := gate.acquire(ctx, footprint); err != nil {
				setErr(err)
				return
			}
			errMu.Lock()
			failed := firstErr != nil
			errMu.Unlock()
			if failed {
				gate.release(footprint)
				return
			}
			jobs <- job{index: i, group: group, footprint: footprint}
		}
	}()

	wg.Wait()

	errMu.Lock()
	defer errMu.Unlock()
	if firstErr != nil {
		return results, firstErr
	}
	for _, sg := range results {
		if sg.err != nil {
			return results, fmt.Errorf("stage group %d: %w", sg.index, sg.err)
		}
	}
	return results, nil
}

func commitStagedGroup(
	ctx context.Context,
	tbl *table.Table,
	sg stagedGroup,
	morCompaction bool,
	rewrittenPaths map[string]struct{},
	snapshotProps iceberg.Properties,
	beforeMeta validate.MetadataSnapshot,
) (groupCommitOutcome, error) {
	var zero groupCommitOutcome
	if err := ctx.Err(); err != nil {
		return zero, fmt.Errorf("context cancelled before staged commit: %w", err)
	}

	progress.Phase("rewrite_data_files", "commit_started", sg.index, 0,
		uint64(len(sg.result.NewDataFiles)), uint64(sg.result.BytesAfter))

	groupTxn := tbl.NewTransaction()
	if err := applyCompactionTestProperties(groupTxn); err != nil {
		return zero, fmt.Errorf("configure compaction test properties: %w", err)
	}
	if morCompaction {
		if err := ensureMORCompactionProperties(groupTxn, tbl.Metadata().Properties()); err != nil {
			return zero, fmt.Errorf("configure MOR compaction properties: %w", err)
		}
	}

	rewrite := groupTxn.NewRewrite(snapshotProps)
	rewrite.ApplyResult(sg.result)
	if morCompaction {
		groupRewritten := rewrittenPathsForGroup(rewrittenPaths, sg.group)
		deadEqDeletes, eqErr := collectDeadEqualityDeletesForGroup(ctx, tbl, groupRewritten)
		if eqErr != nil {
			return zero, fmt.Errorf("collect dead eq deletes: %w", eqErr)
		}
		for _, df := range deadEqDeletes {
			rewrite.DeleteFile(df)
		}
	}

	if err := rewrite.Commit(ctx); err != nil {
		return zero, fmt.Errorf("stage rewrite snapshot for group %d: %w", sg.index, err)
	}

	testhook.BeforeCommit()

	updated, commitErr := groupTxn.Commit(ctx)
	if commitErr != nil {
		return zero, fmt.Errorf("commit rewrite_data_files group %d: %w", sg.index, commitErr)
	}
	if err := validate.AssertMetadataStable(beforeMeta, updated); err != nil {
		return zero, err
	}

	progress.Phase("rewrite_data_files", "commit_done", sg.index, 0,
		uint64(len(sg.result.NewDataFiles)), uint64(sg.result.BytesAfter))

	return groupCommitOutcome{
		committed:    true,
		outputFiles:  uint64(len(sg.result.NewDataFiles)),
		bytesRemoved: uint64(sg.result.BytesBefore),
		bytesWritten: uint64(sg.result.BytesAfter),
	}, nil
}
