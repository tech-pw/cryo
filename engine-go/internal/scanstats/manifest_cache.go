package scanstats

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/apache/iceberg-go"

	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
)

const manifestCacheSchemaVersion = 1

type cachedManifestDoc struct {
	Version      int                   `json:"version"`
	ManifestPath string                `json:"manifest_path"`
	Entries      []cachedManifestEntry `json:"entries"`
}

type cachedManifestEntry struct {
	FilePath        string            `json:"file_path"`
	FileSizeInBytes *int64            `json:"file_size_in_bytes"`
	RecordCount     int64             `json:"record_count"`
	Content         string            `json:"content"`
	FileFormat      string            `json:"file_format"`
	SpecID          *int32            `json:"spec_id"`
	PartitionValues map[string]any    `json:"partition_values"`
	NullValueCounts map[string]int64  `json:"null_value_counts"`
	ValueCounts     map[string]int64  `json:"value_counts"`
	LowerBounds     map[string]string `json:"lower_bounds"`
	UpperBounds     map[string]string `json:"upper_bounds"`
	SnapshotID      *int64            `json:"snapshot_id"`
}

func manifestCacheKey(manifestPath string) string {
	sum := sha256.Sum256([]byte("v" + strconv.Itoa(manifestCacheSchemaVersion) + ":" + manifestPath))
	return hex.EncodeToString(sum[:])
}

func manifestCacheFilePath(cacheDir, manifestPath string) string {
	key := manifestCacheKey(manifestPath)
	return filepath.Join(cacheDir, key[:2], key+".json")
}

func loadCachedManifest(cacheDir, manifestPath string, maxBytes int64) ([]cachedManifestEntry, bool) {
	cacheDir = strings.TrimSpace(cacheDir)
	if cacheDir == "" || maxBytes <= 0 || manifestPath == "" {
		return nil, false
	}
	target := manifestCacheFilePath(cacheDir, manifestPath)
	raw, err := os.ReadFile(target)
	if err != nil {
		return nil, false
	}
	now := time.Now()
	_ = os.Chtimes(target, now, now)
	var doc cachedManifestDoc
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, false
	}
	if doc.Version != manifestCacheSchemaVersion || doc.ManifestPath != manifestPath {
		return nil, false
	}
	return doc.Entries, true
}

func saveCachedManifest(cacheDir, manifestPath string, maxBytes int64, entries []cachedManifestEntry) {
	cacheDir = strings.TrimSpace(cacheDir)
	if cacheDir == "" || maxBytes <= 0 || manifestPath == "" {
		return
	}
	payload, err := json.Marshal(cachedManifestDoc{
		Version:      manifestCacheSchemaVersion,
		ManifestPath: manifestPath,
		Entries:      entries,
	})
	if err != nil || int64(len(payload)) > maxBytes {
		return
	}
	target := manifestCacheFilePath(cacheDir, manifestPath)
	dir := filepath.Dir(target)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return
	}
	tmp, err := os.CreateTemp(dir, ".tmp-*")
	if err != nil {
		return
	}
	tmpName := tmp.Name()
	if _, err := tmp.Write(payload); err != nil {
		_ = tmp.Close()
		_ = os.Remove(tmpName)
		return
	}
	if err := tmp.Close(); err != nil {
		_ = os.Remove(tmpName)
		return
	}
	if err := os.Rename(tmpName, target); err != nil {
		_ = os.Remove(tmpName)
		return
	}
	evictManifestCacheLRU(cacheDir, maxBytes)
}

type cacheFileStat struct {
	path  string
	mtime time.Time
	size  int64
}

func evictManifestCacheLRU(cacheDir string, maxBytes int64) {
	subdirs, err := os.ReadDir(cacheDir)
	if err != nil {
		return
	}
	var files []cacheFileStat
	var total int64
	for _, sub := range subdirs {
		if !sub.IsDir() {
			continue
		}
		subPath := filepath.Join(cacheDir, sub.Name())
		entries, err := os.ReadDir(subPath)
		if err != nil {
			continue
		}
		for _, item := range entries {
			if item.IsDir() || !strings.HasSuffix(item.Name(), ".json") {
				continue
			}
			info, err := item.Info()
			if err != nil {
				continue
			}
			total += info.Size()
			files = append(files, cacheFileStat{
				path:  filepath.Join(subPath, item.Name()),
				mtime: info.ModTime(),
				size:  info.Size(),
			})
		}
	}
	if total <= maxBytes {
		return
	}
	sort.Slice(files, func(i, j int) bool { return files[i].mtime.Before(files[j].mtime) })
	for _, victim := range files {
		if total <= maxBytes {
			break
		}
		if err := os.Remove(victim.path); err == nil {
			total -= victim.size
		}
	}
}

func summarizeLiveEntry(
	entry iceberg.ManifestEntry,
	df iceberg.DataFile,
	acc *accumulator,
	defaultFields []protocol.ScanStatsPartitionField,
) cachedManifestEntry {
	size := df.FileSizeBytes()
	specID := df.SpecID()
	specFields := acc.fieldsForSpec(specID, defaultFields)
	nulls, vals, values, note := readDataFileMaps(
		df.NullValueCounts,
		df.ValueCounts,
		func() map[string]any { return partitionValues(df, specFields) },
	)
	if note != "" {
		acc.noteManifestError(note)
	}
	lowerRaw, upperRaw, boundsOK := readBoundMaps(df)
	nullStr := make(map[string]int64, len(nulls))
	for k, v := range nulls {
		nullStr[strconv.Itoa(k)] = v
	}
	valStr := make(map[string]int64, len(vals))
	for k, v := range vals {
		valStr[strconv.Itoa(k)] = v
	}
	lowB64 := map[string]string{}
	highB64 := map[string]string{}
	if boundsOK {
		for k, v := range lowerRaw {
			if v != nil {
				lowB64[strconv.Itoa(k)] = base64.StdEncoding.EncodeToString(v)
			}
		}
		for k, v := range upperRaw {
			if v != nil {
				highB64[strconv.Itoa(k)] = base64.StdEncoding.EncodeToString(v)
			}
		}
	}
	var snapID *int64
	if id := entry.SnapshotID(); id != -1 {
		copied := id
		snapID = &copied
	}
	content := ""
	switch df.ContentType() {
	case iceberg.EntryContentEqDeletes:
		content = "EQUALITY_DELETES"
	case iceberg.EntryContentPosDeletes:
		content = "POSITION_DELETES"
	default:
		content = "DATA"
	}
	return cachedManifestEntry{
		FilePath:        df.FilePath(),
		FileSizeInBytes: &size,
		RecordCount:     df.Count(),
		Content:         content,
		FileFormat:      string(df.FileFormat()),
		SpecID:          &specID,
		PartitionValues: values,
		NullValueCounts: nullStr,
		ValueCounts:     valStr,
		LowerBounds:     lowB64,
		UpperBounds:     highB64,
		SnapshotID:      snapID,
	}
}

func (a *accumulator) addCachedEntry(item cachedManifestEntry, times map[int64]int64) {
	var size int64
	if item.FileSizeInBytes == nil {
		a.unknownFileSizes++
	} else {
		size = *item.FileSizeInBytes
		if deleteKind(item.Content) != "" {
			recordFileSizeBin(a.deleteSizeBins, size)
		} else {
			recordFileSizeBin(a.dataSizeBins, size)
		}
	}
	if item.FileFormat != "" {
		a.fileFormats[strings.ToUpper(item.FileFormat)] = struct{}{}
	}
	var specID int32
	if item.SpecID != nil {
		specID = *item.SpecID
	}
	writeMs, hasWrite := cachedSnapshotTime(item.SnapshotID, times)
	values := make(map[string]any, len(item.PartitionValues))
	for k, v := range item.PartitionValues {
		values[k] = jsonable(v)
	}
	kind := deleteKind(item.Content)
	if kind != "" {
		a.deleteFiles++
		a.deleteBytes += size
		if kind == "equality" {
			a.equalityDeleteFiles++
		} else {
			a.positionDeleteFiles++
		}
		acc := a.partitionFor(specID, values)
		acc.deleteFiles++
		if kind == "equality" {
			acc.equalityDeleteFiles++
			acc.equalityDeleteBytes += size
		}
		acc.observeWrite(writeMs, hasWrite)
		return
	}
	a.totalFiles++
	a.totalSizeBytes += size
	a.totalRecords += item.RecordCount
	if size < a.opts.HealthSmallFileBytes {
		a.healthSmallFiles++
	}
	a.countBelowTarget(size)

	nulls := make(map[int]int64, len(item.NullValueCounts))
	for k, v := range item.NullValueCounts {
		if id, err := strconv.Atoi(k); err == nil {
			nulls[id] = v
		}
	}
	vals := make(map[int]int64, len(item.ValueCounts))
	for k, v := range item.ValueCounts {
		if id, err := strconv.Atoi(k); err == nil {
			vals[id] = v
		}
	}
	lowerRaw := make(map[int][]byte, len(item.LowerBounds))
	for k, v := range item.LowerBounds {
		if id, err := strconv.Atoi(k); err == nil {
			if decoded, err := base64.StdEncoding.DecodeString(v); err == nil {
				lowerRaw[id] = decoded
			}
		}
	}
	upperRaw := make(map[int][]byte, len(item.UpperBounds))
	for k, v := range item.UpperBounds {
		if id, err := strconv.Atoi(k); err == nil {
			if decoded, err := base64.StdEncoding.DecodeString(v); err == nil {
				upperRaw[id] = decoded
			}
		}
	}
	a.addColumnStats(nulls, vals)
	a.mergeColumnBounds(lowerRaw, upperRaw, true, nulls, vals)
	a.addCachedFileSample(item.FilePath, size, lowerRaw, upperRaw)

	acc := a.partitionFor(specID, values)
	acc.files++
	acc.sizeBytes += size
	if float64(size) < a.opts.SmallFileMB*float64(mb) {
		acc.small++
	}
	acc.observeWrite(writeMs, hasWrite)
	if acc.samplePath == "" && item.FilePath != "" {
		acc.samplePath = item.FilePath
	}
}

func (a *accumulator) addCachedFileSample(filePath string, size int64, lowerRaw, upperRaw map[int][]byte) {
	if a.opts.MaxFileSamples <= 0 || len(a.opts.SampleColumns) == 0 || filePath == "" {
		return
	}
	columns := a.boundSafeSampleColumns()
	lower := keepColumns(decodeBounds(lowerRaw, a.columnDefs, a.columnTypes), columns)
	upper := keepColumns(decodeBounds(upperRaw, a.columnDefs, a.columnTypes), columns)
	if len(lower) == 0 || len(upper) == 0 {
		return
	}
	a.fileSamples[filePath] = protocol.ScanStatsFileBoundSample{
		FilePath:    filePath,
		SizeBytes:   size,
		LowerBounds: lower,
		UpperBounds: upper,
	}
	if len(a.fileSamples) <= a.opts.MaxFileSamples {
		return
	}
	var largest string
	for path := range a.fileSamples {
		if path > largest {
			largest = path
		}
	}
	delete(a.fileSamples, largest)
}

func cachedSnapshotTime(snapID *int64, times map[int64]int64) (int64, bool) {
	if snapID == nil || *snapID == -1 {
		return 0, false
	}
	if ms, ok := times[*snapID]; ok {
		return ms, true
	}
	oldest, ok := times[oldestSnapshotKey]
	return oldest, ok
}
