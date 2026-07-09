package ops

import (
	"context"
	"errors"
	"fmt"
	"runtime"

	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/table"
	"github.com/apache/iceberg-go/table/compaction"

	"github.com/cryo-lake/cryo/engine-go/internal/progress"
	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
	"github.com/cryo-lake/cryo/engine-go/internal/testhook"
	"github.com/cryo-lake/cryo/engine-go/internal/validate"
)

// commitRaceLostToken is a stable, machine-parseable prefix the Python worker/reporting
// layer matches to surface "compaction is losing the commit race on table X" as a
// first-class alert instead of a generic failure buried in a retry loop (scoping doc
// §3.2 / Scenario B).
const commitRaceLostToken = "commit_race_lost"

// errCommitRaceLost marks a rewrite that repeatedly lost the optimistic-concurrency
// commit race until its checkpoint rounds were exhausted. It wraps the last underlying
// commit conflict so errors.Is(err, table.ErrCommitFailed) still holds upstream.
var errCommitRaceLost = errors.New(commitRaceLostToken)

func isCommitRaceLost(err error) bool {
	return errors.Is(err, errCommitRaceLost)
}

// rewriteErrorMessage renders a rewrite failure for the OperationResult/progress line,
// tagging commit-race losses with commitRaceLostToken so the caller can classify them.
func rewriteErrorMessage(err error) string {
	if err == nil {
		return ""
	}
	if isCommitRaceLost(err) || isRetryableCommitError(err) {
		return fmt.Sprintf("%s: %s", commitRaceLostToken, err.Error())
	}
	return err.Error()
}

type groupCommitOutcome struct {
	committed    bool
	outputFiles  uint64
	bytesRemoved uint64
	bytesWritten uint64
}

func (r *rewriteOnceResult) add(o groupCommitOutcome) {
	r.outputFiles += o.outputFiles
	r.bytesRemoved += o.bytesRemoved
	r.bytesWritten += o.bytesWritten
}

// executePartialProgressResumable commits each planned group as its own atomic Iceberg
// snapshot (Iceberg's PARTIAL_PROGRESS_ENABLED precedent). Unlike a single all-or-nothing
// commit, a per-group commit-race loss only forfeits the current group, not the run:
// on a retryable conflict we reload the table, re-plan from the *current* snapshot (so
// already-committed groups are naturally excluded — their small files are gone), and
// continue with whatever still needs compaction. Rounds are bounded; if we still cannot
// land a group after appCommitRetries reloads, the run fails with errCommitRaceLost so
// the caller surfaces it as a first-class alert rather than looping forever.
func executePartialProgressResumable(
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
	const operation = "rewrite_data_files"
	var totals rewriteOnceResult

	// Metadata identity (schema/spec/sort-order ids) must stay stable across every
	// per-group commit; captured once from the pre-compaction table.
	beforeMeta := validate.CaptureMetadataSnapshot(tbl)
	rewriteOpts := table.RewriteDataFilesOptions{
		SnapshotProps: validate.CustomSnapshotProps(tbl),
		GroupOptions:  rewriteGroupOptions(cfg.TargetFileSizeBytes, op.Params),
	}

	maxRounds := appCommitRetries + 1
	committedGroups := 0
	var lastConflict error

	for round := range maxRounds {
		if round > 0 {
			reloaded, err := reloadTable(ctx, req)
			if err != nil {
				return totals, fmt.Errorf("reload table before checkpoint round %d: %w", round, err)
			}
			tbl = reloaded

			replanned, replannedPaths, err := planCheckpointGroups(
				ctx, tbl, cfg, rowFilter, op.Predicates, groupSplitLimitsFromParams(op.Params),
			)
			if err != nil {
				return totals, err
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
			rewriteOpts.SnapshotProps = validate.CustomSnapshotProps(tbl)
		}

		if len(groups) == 0 {
			// Nothing (more) to compact — either the plan was empty or a prior round
			// already landed every group (idempotent resume).
			return totals, nil
		}

		limits := groupSplitLimitsFromParams(op.Params)
		conflicted := false
		for i := range groups {
			group := groups[i]
			progress.GroupStarted(
				operation,
				group.PartitionKey,
				len(group.Tasks),
				uint64(groupCompressedBytes(group)),
				limits.maxFiles,
				uint64(limits.maxBytes),
				i,
				len(groups),
			)
			outcome, err := commitCheckpointGroup(
				ctx, tbl, group, morCompaction, rewrittenPaths, rewriteOpts, beforeMeta,
			)
			if err != nil {
				if isRetryableCommitError(err) {
					lastConflict = err
					conflicted = true
					break
				}
				return totals, err
			}
			if outcome.committed {
				committedGroups++
				totals.add(outcome)
				progress.Checkpoint(operation, committedGroups, len(groups), totals.outputFiles, totals.bytesWritten)
			}
			progress.Progress(operation, uint8((i+1)*100/len(groups)), totals.outputFiles, totals.bytesWritten)
			// Release Arrow/Parquet buffers between groups so RSS does not ratchet
			// across a long partial-progress run on dense partitions.
			runtime.GC()
			if err := tbl.Refresh(ctx); err != nil {
				return totals, fmt.Errorf("refresh table after group %d: %w", i, err)
			}
		}

		if !conflicted {
			return totals, nil
		}
	}

	// Join both sentinels: errors.Is(err, errCommitRaceLost) classifies the alert, and
	// errors.Is(err, table.ErrCommitFailed) is preserved for upstream conflict checks.
	return totals, errors.Join(
		errCommitRaceLost,
		fmt.Errorf("rewrite_data_files exhausted %d checkpoint rounds: %w", maxRounds, lastConflict),
	)
}

// commitCheckpointGroup rewrites and atomically commits a single compaction group.
// An empty group (all inputs already gone after a reload) is an idempotent no-op.
func commitCheckpointGroup(
	ctx context.Context,
	tbl *table.Table,
	group table.CompactionTaskGroup,
	morCompaction bool,
	rewrittenPaths map[string]struct{},
	rewriteOpts table.RewriteDataFilesOptions,
	beforeMeta validate.MetadataSnapshot,
) (groupCommitOutcome, error) {
	var zero groupCommitOutcome
	if len(group.Tasks) == 0 {
		return zero, nil
	}
	if err := ctx.Err(); err != nil {
		return zero, fmt.Errorf("context cancelled before group commit: %w", err)
	}

	groupTxn := tbl.NewTransaction()
	if err := applyCompactionTestProperties(groupTxn); err != nil {
		return zero, fmt.Errorf("configure compaction test properties: %w", err)
	}
	if morCompaction {
		if err := ensureMORCompactionProperties(groupTxn, tbl.Metadata().Properties()); err != nil {
			return zero, fmt.Errorf("configure MOR compaction properties: %w", err)
		}
		groupRewritten := rewrittenPathsForGroup(rewrittenPaths, group)
		deadEqDeletes, eqErr := collectDeadEqualityDeletesForGroup(ctx, tbl, groupRewritten)
		if eqErr != nil {
			return zero, fmt.Errorf("collect dead eq deletes: %w", eqErr)
		}
		rewriteOpts.ExtraDeleteFilesToRemove = deadEqDeletes
	}

	groupResult, err := groupTxn.RewriteDataFiles(ctx, []table.CompactionTaskGroup{group}, rewriteOpts)
	if err != nil {
		return zero, fmt.Errorf("rewrite_data_files group: %w", err)
	}
	if groupResult == nil || groupResult.RewrittenGroups == 0 {
		return zero, nil
	}

	testhook.BeforeCommit()

	updated, commitErr := groupTxn.Commit(ctx)
	if commitErr != nil {
		return zero, fmt.Errorf("commit rewrite_data_files group: %w", commitErr)
	}
	if err := validate.AssertMetadataStable(beforeMeta, updated); err != nil {
		return zero, err
	}

	return groupCommitOutcome{
		committed:    true,
		outputFiles:  uint64(groupResult.AddedDataFiles),
		bytesRemoved: uint64(groupResult.BytesBefore),
		bytesWritten: uint64(groupResult.BytesAfter),
	}, nil
}

// planCheckpointGroups re-derives the compaction groups from the table's current snapshot.
// Used on resume/conflict rounds: files compacted by earlier rounds are no longer selected,
// so continuing simply skips them (idempotent resume).
func planCheckpointGroups(
	ctx context.Context,
	tbl *table.Table,
	cfg compaction.Config,
	rowFilter iceberg.BooleanExpression,
	predicates []protocol.PredicateRequest,
	limits groupSplitLimits,
) ([]table.CompactionTaskGroup, map[string]struct{}, error) {
	tasks, err := planScopedTasks(ctx, tbl, rowFilter, predicates)
	if err != nil {
		return nil, nil, err
	}
	if len(tasks) == 0 {
		return nil, nil, nil
	}
	plan, err := cfg.PlanCompaction(tasks)
	if err != nil {
		return nil, nil, fmt.Errorf("re-plan compaction: %w", err)
	}
	groups := finalizeRewriteGroups(toTaskGroups(plan.Groups), limits, int(cfg.MinInputFiles))
	return groups, rewrittenPathsFromPlan(plan), nil
}

func collectGroupTasks(groups []table.CompactionTaskGroup) []table.FileScanTask {
	tasks := make([]table.FileScanTask, 0)
	for _, group := range groups {
		tasks = append(tasks, group.Tasks...)
	}
	return tasks
}
