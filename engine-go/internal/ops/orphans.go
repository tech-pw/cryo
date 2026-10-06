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

const defaultOrphanMinAgeHours int64 = 72

func RemoveOrphanFiles(ctx context.Context, req *protocol.ExecuteRequest, op *protocol.OperationRequest) protocol.OperationResult {
	const operation = "remove_orphan_files"
	progress.Started(operation)

	_, tbl, err := catalog.LoadTable(ctx, req)
	if err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, err)
	}

	minAgeHours := orphanMinAgeHours(op.Params)
	olderThan := time.Duration(minAgeHours) * time.Hour
	dryRun := !isExecuteMode(req.Mode)

	opts := []table.OrphanCleanupOption{
		table.WithFilesOlderThan(olderThan),
	}
	if maxConcurrency := intParam(op.Params, "max_concurrency", 0); maxConcurrency > 0 {
		opts = append(opts, table.WithMaxConcurrency(maxConcurrency))
	}

	previewOpts := append(append([]table.OrphanCleanupOption{}, opts...), table.WithDryRun(true))
	preview, err := tbl.DeleteOrphanFiles(ctx, previewOpts...)
	if err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, fmt.Errorf("remove_orphan_files preview: %w", err))
	}

	bytesRemoved, err := orphanBytes(ctx, tbl, preview.OrphanFileLocations)
	if err != nil {
		progress.Failed(operation, err.Error())
		return failResult(operation, err)
	}

	var result table.OrphanCleanupResult
	if dryRun {
		result = preview
	} else {
		executeOpts := append(append([]table.OrphanCleanupOption{}, opts...), table.WithDryRun(false))
		result, err = tbl.DeleteOrphanFiles(ctx, executeOpts...)
		if err != nil {
			progress.Failed(operation, err.Error())
			return failResult(operation, fmt.Errorf("remove_orphan_files: %w", err))
		}
	}

	orphanCount := uint64(len(preview.OrphanFileLocations))
	listedCount := orphanCount

	progress.Progress(operation, 50, orphanCount, bytesRemoved)

	deletedCount := uint64(len(result.DeletedFiles))
	if dryRun {
		deletedCount = orphanCount
	}

	progress.Completed(operation, deletedCount, bytesRemoved)
	return protocol.OperationResult{
		Operation:   operation,
		Success:     true,
		InputFiles:  &listedCount,
		OutputFiles: &deletedCount,
		BytesRemoved: &bytesRemoved,
		DurationMS:  0,
	}
}

func orphanMinAgeHours(raw json.RawMessage) int64 {
	if hours := int64Param(raw, "min_age_hours", -1); hours >= 0 {
		return hours
	}
	if hours := int64Param(raw, "orphan_min_age_hours", -1); hours >= 0 {
		return hours
	}
	if days := int64Param(raw, "retention_days", -1); days >= 0 {
		return days * 24
	}
	return defaultOrphanMinAgeHours
}

func int64Param(raw json.RawMessage, key string, defaultValue int64) int64 {
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
	var asInt int64
	if err := json.Unmarshal(value, &asInt); err == nil {
		return asInt
	}
	var asFloat float64
	if err := json.Unmarshal(value, &asFloat); err == nil {
		return int64(asFloat)
	}
	return defaultValue
}

func orphanBytes(ctx context.Context, tbl *table.Table, paths []string) (uint64, error) {
	if len(paths) == 0 {
		return 0, nil
	}

	fs, err := tbl.FS(ctx)
	if err != nil {
		return 0, fmt.Errorf("file io: %w", err)
	}

	var total uint64
	for _, path := range paths {
		file, err := fs.Open(path)
		if err != nil {
			continue
		}
		info, err := file.Stat()
		_ = file.Close()
		if err != nil {
			continue
		}
		if info.Size() > 0 {
			total += uint64(info.Size())
		}
	}
	return total, nil
}
