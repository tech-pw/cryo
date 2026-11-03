package ops

import (
	"context"
	"errors"
	"fmt"
	iofs "io/fs"
	"sync"

	"github.com/apache/iceberg-go"
	iceio "github.com/apache/iceberg-go/io"
	"github.com/apache/iceberg-go/table"
	"gocloud.dev/gcerrors"
)

const (
	defaultExpireDeleteConcurrency = 16
	maxReportedDeleteErrors        = 5
)

type expiredFileCleanup struct {
	candidates uint64
	deleted    uint64
	bytes      uint64
}

// deleteExpiredSnapshotFiles removes the files that only the snapshots present in before and
// gone from after referenced: their manifest lists, manifests no retained snapshot lists, data
// and delete files no retained manifest holds live, and their statistics files. This is the set
// Spark's and Trino's expire_snapshots delete. Leaving it to remove_orphan_files leaks it on
// every run that plans no orphan removal (standard/light mode, explicit expire-only requests,
// tables whose location overlaps another table's).
func deleteExpiredSnapshotFiles(ctx context.Context, before, after *table.Table, concurrency int) (expiredFileCleanup, error) {
	retainedIDs := make(map[int64]struct{})
	for _, snap := range after.Metadata().Snapshots() {
		retainedIDs[snap.SnapshotID] = struct{}{}
	}
	var expired []table.Snapshot
	for _, snap := range before.Metadata().Snapshots() {
		if _, ok := retainedIDs[snap.SnapshotID]; !ok {
			expired = append(expired, snap)
		}
	}
	if len(expired) == 0 {
		return expiredFileCleanup{}, nil
	}

	fs, err := before.FS(ctx)
	if err != nil {
		return expiredFileCleanup{}, fmt.Errorf("open table filesystem: %w", err)
	}

	candidates, err := expiredSnapshotFiles(fs, before, after, expired)
	if err != nil {
		return expiredFileCleanup{}, err
	}
	return removeFiles(ctx, fs, candidates, concurrency)
}

// expiredSnapshotFiles maps each file reachable only from the expired snapshots to its size in
// bytes (0 when the metadata does not record it).
func expiredSnapshotFiles(fs iceio.IO, before, after *table.Table, expired []table.Snapshot) (map[string]int64, error) {
	retainedLists := make(map[string]struct{})
	retainedManifests := make(map[string]iceberg.ManifestFile)
	for _, snap := range after.Metadata().Snapshots() {
		if snap.ManifestList != "" {
			retainedLists[snap.ManifestList] = struct{}{}
		}
		manifests, err := snap.Manifests(fs)
		if err != nil {
			return nil, fmt.Errorf("read manifests of retained snapshot %d: %w", snap.SnapshotID, err)
		}
		for _, manifest := range manifests {
			retainedManifests[manifest.FilePath()] = manifest
		}
	}

	candidates := make(map[string]int64)
	contentFiles := make(map[string]struct{})
	visited := make(map[string]struct{})
	expiredIDs := make(map[int64]struct{}, len(expired))
	for _, snap := range expired {
		expiredIDs[snap.SnapshotID] = struct{}{}
		if snap.ManifestList != "" {
			if _, ok := retainedLists[snap.ManifestList]; !ok {
				candidates[snap.ManifestList] = 0
			}
		}
		manifests, err := snap.Manifests(fs)
		if err != nil {
			return nil, fmt.Errorf("read manifests of expired snapshot %d: %w", snap.SnapshotID, err)
		}
		for _, manifest := range manifests {
			path := manifest.FilePath()
			if _, ok := retainedManifests[path]; ok {
				continue
			}
			if _, ok := visited[path]; ok {
				continue
			}
			visited[path] = struct{}{}
			candidates[path] = manifest.Length()
			for entry, err := range manifest.Entries(fs, false) {
				if err != nil {
					return nil, fmt.Errorf("read entries of expired manifest %s: %w", path, err)
				}
				file := entry.DataFile()
				candidates[file.FilePath()] = file.FileSizeBytes()
				contentFiles[file.FilePath()] = struct{}{}
			}
		}
	}

	if len(contentFiles) > 0 {
		for path, manifest := range retainedManifests {
			for entry, err := range manifest.Entries(fs, true) {
				if err != nil {
					return nil, fmt.Errorf("read entries of retained manifest %s: %w", path, err)
				}
				delete(candidates, entry.DataFile().FilePath())
			}
		}
	}

	retainedStats := make(map[string]struct{})
	for sf := range after.Metadata().Statistics() {
		retainedStats[sf.StatisticsPath] = struct{}{}
	}
	for psf := range after.Metadata().PartitionStatistics() {
		retainedStats[psf.StatisticsPath] = struct{}{}
	}
	for sf := range before.Metadata().Statistics() {
		if _, ok := expiredIDs[sf.SnapshotID]; ok && sf.StatisticsPath != "" {
			if _, kept := retainedStats[sf.StatisticsPath]; !kept {
				candidates[sf.StatisticsPath] = sf.FileSizeInBytes
			}
		}
	}
	for psf := range before.Metadata().PartitionStatistics() {
		if _, ok := expiredIDs[psf.SnapshotID]; ok && psf.StatisticsPath != "" {
			if _, kept := retainedStats[psf.StatisticsPath]; !kept {
				candidates[psf.StatisticsPath] = psf.FileSizeInBytes
			}
		}
	}

	return candidates, nil
}

func removeFiles(ctx context.Context, fs iceio.IO, files map[string]int64, concurrency int) (expiredFileCleanup, error) {
	result := expiredFileCleanup{candidates: uint64(len(files))}
	if len(files) == 0 {
		return result, nil
	}
	if concurrency <= 0 {
		concurrency = defaultExpireDeleteConcurrency
	}

	type job struct {
		path string
		size int64
	}
	jobs := make(chan job)
	var (
		mu       sync.Mutex
		wg       sync.WaitGroup
		failures []error
		failed   int
	)
	for range min(concurrency, len(files)) {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for item := range jobs {
				err := fs.Remove(item.path)
				mu.Lock()
				switch {
				case err == nil:
					result.deleted++
					if item.size > 0 {
						result.bytes += uint64(item.size)
					}
				case isNotExist(err):
				default:
					failed++
					if len(failures) < maxReportedDeleteErrors {
						failures = append(failures, fmt.Errorf("%s: %w", item.path, err))
					}
				}
				mu.Unlock()
			}
		}()
	}
	for path, size := range files {
		if ctx.Err() != nil {
			break
		}
		jobs <- job{path: path, size: size}
	}
	close(jobs)
	wg.Wait()

	if err := ctx.Err(); err != nil {
		return result, err
	}
	if failed > 0 {
		return result, fmt.Errorf("%d of %d file deletes failed: %w", failed, len(files), errors.Join(failures...))
	}
	return result, nil
}

func isNotExist(err error) bool {
	return errors.Is(err, iofs.ErrNotExist) || gcerrors.Code(err) == gcerrors.NotFound
}
