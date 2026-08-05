// Package scanstats streams current-snapshot Iceberg manifests and emits
// partition/file aggregates compatible with Cryo's Python scanner contract
// (docs/scanner-data-points.md). It deliberately avoids PlanFiles().
package scanstats

import (
	"context"
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/table"

	"github.com/cryo-lake/cryo/engine-go/internal/catalog"
	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
)

const (
	defaultSmallFileMB          = 89.6
	defaultHealthSmallFileBytes = 8 * 1024 * 1024
	defaultMaxPartitions        = 5000
	defaultMaxFileSamples       = 1000
	mb                          = 1024 * 1024
)

// FileRecord is a synthetic manifest entry for unit tests / parity fixtures.
type FileRecord struct {
	SizeBytes       int64
	RecordCount     int64
	Content         string // "DATA", "POSITION_DELETES", "EQUALITY_DELETES", "1", "2"
	FileFormat      string
	SpecID          int32
	Partition       map[string]any // partition_field name → value
	NullValueCounts map[int]int64
	ValueCounts     map[int]int64
}

// AggregateFromRecords mirrors the Python analyze loop without Iceberg I/O.
func AggregateFromRecords(
	records []FileRecord,
	fields []protocol.ScanStatsPartitionField,
	opts Options,
) protocol.ScanStatsResponse {
	opts = opts.withDefaults()
	acc := newAccumulator(fields, opts)
	for _, rec := range records {
		acc.addRecord(rec)
	}
	resp := acc.finish("", "", nil)
	resp.OK = true
	return resp
}

// Options controls thresholds for a scan-stats pass.
type Options struct {
	SmallFileMB          float64
	HealthSmallFileBytes int64
	MaxPartitions        int
	MaxFileSamples       int
}

func (o Options) withDefaults() Options {
	if o.SmallFileMB <= 0 {
		o.SmallFileMB = defaultSmallFileMB
	}
	if o.HealthSmallFileBytes <= 0 {
		o.HealthSmallFileBytes = defaultHealthSmallFileBytes
	}
	if o.MaxPartitions <= 0 {
		o.MaxPartitions = defaultMaxPartitions
	}
	if o.MaxFileSamples < 0 {
		o.MaxFileSamples = 0
	} else if o.MaxFileSamples == 0 {
		o.MaxFileSamples = defaultMaxFileSamples
	}
	return o
}

// Run loads the table via iceberg-go and streams current-snapshot manifests.
func Run(ctx context.Context, req *protocol.ScanStatsRequest) protocol.ScanStatsResponse {
	if req == nil {
		err := "nil scan-stats request"
		return protocol.ScanStatsResponse{OK: false, Error: &err}
	}
	exec := req.ToExecuteRequest()
	ctx = catalog.WithSourceAWSConfig(ctx, exec)
	_, tbl, err := catalog.LoadTable(ctx, exec)
	if err != nil {
		msg := err.Error()
		return protocol.ScanStatsResponse{OK: false, Error: &msg}
	}
	return ScanTable(ctx, tbl, Options{
		SmallFileMB:          req.SmallFileMB,
		HealthSmallFileBytes: req.HealthSmallFileBytes,
		MaxPartitions:        req.MaxPartitions,
		MaxFileSamples:       req.MaxFileSamples,
	})
}

// ScanTable walks the current snapshot of an already-loaded table.
// Panics from iceberg-go (seen in practice on nil date/timestamp partition values
// during NullValueCounts → initializeMapData) are recovered into a failed response
// so the subprocess exits cleanly instead of exit=2 with no JSON.
func ScanTable(ctx context.Context, tbl *table.Table, opts Options) (resp protocol.ScanStatsResponse) {
	opts = opts.withDefaults()
	schemaName, tableName := splitIdent(tbl.Identifier())
	defer func() {
		if r := recover(); r != nil {
			msg := fmt.Sprintf("panic in scan-stats: %v", r)
			resp = protocol.ScanStatsResponse{OK: false, Error: &msg, SchemaName: schemaName, TableName: tableName}
		}
	}()

	fields := partitionFields(tbl)
	acc := newAccumulator(fields, opts)
	acc.columnDefs = columnDefsFromSchema(tbl.Schema())
	acc.columnTypes = columnTypesFromSchema(tbl.Schema())

	fs, err := tbl.FS(ctx)
	if err != nil {
		msg := fmt.Sprintf("open filesystem: %v", err)
		return protocol.ScanStatsResponse{OK: false, Error: &msg, SchemaName: schemaName, TableName: tableName}
	}

	snap := tbl.CurrentSnapshot()
	var manifests []iceberg.ManifestFile
	if snap != nil {
		manifests, err = snap.Manifests(fs)
		if err != nil {
			msg := fmt.Sprintf("load manifests: %v", err)
			return protocol.ScanStatsResponse{OK: false, Error: &msg, SchemaName: schemaName, TableName: tableName}
		}
	}
	acc.manifestCount = len(manifests)
	for _, m := range manifests {
		acc.manifestTotalBytes += m.Length()
		for entry, entryErr := range m.Entries(fs, true) {
			if entryErr != nil {
				acc.manifestErrors = append(acc.manifestErrors, entryErr.Error())
				break
			}
			if entry == nil {
				continue
			}
			df := entry.DataFile()
			if df == nil {
				continue
			}
			acc.addDataFile(df, fields)
		}
	}

	meta := snapshotMeta(tbl)
	props := tableProps(tbl)
	formatVersion := detectFormatVersion(tbl)
	resp = acc.finish(schemaName, tableName, &meta)
	resp.OK = true
	resp.Location = tbl.Location()
	resp.WriteDistributionMode = props.writeDistributionMode
	resp.HasSortOrder = props.hasSortOrder
	resp.TargetFileSizeBytes = props.targetFileSizeBytes
	resp.IcebergProperties = props.icebergProperties
	resp.GCEnabled = props.gcEnabled
	resp.FormatVersion = formatVersion
	return resp
}

type snapshotInfo struct {
	totalSnapshots    int
	firstSnapshotAt   *string
	lastSnapshotAt    *string
	currentSnapshotID *int64
}

func snapshotMeta(tbl *table.Table) snapshotInfo {
	snaps := tbl.Metadata().Snapshots()
	info := snapshotInfo{totalSnapshots: len(snaps)}
	var first, last int64
	have := false
	for _, s := range snaps {
		ts := s.TimestampMs
		if !have || ts < first {
			first = ts
		}
		if !have || ts > last {
			last = ts
		}
		have = true
	}
	if have {
		f := time.UnixMilli(first).UTC().Format(time.RFC3339Nano)
		l := time.UnixMilli(last).UTC().Format(time.RFC3339Nano)
		info.firstSnapshotAt = &f
		info.lastSnapshotAt = &l
	}
	if cur := tbl.CurrentSnapshot(); cur != nil {
		id := cur.SnapshotID
		info.currentSnapshotID = &id
	}
	return info
}

type propInfo struct {
	writeDistributionMode *string
	hasSortOrder          *bool
	targetFileSizeBytes   *int64
	icebergProperties     map[string]string
	gcEnabled             *bool
}

var trackedIcebergPropertyKeys = []string{
	"write.target-file-size-bytes",
	"write.distribution-mode",
	"write.sort-order",
	"write.metadata.delete-after-commit.enabled",
	"write.metadata.previous-versions-max",
	"commit.retry.num-retries",
	"commit.retry.min-wait-ms",
	"commit.retry.max-wait-ms",
	"write.parquet.compression-codec",
	"write.delete-mode",
	"write.update-mode",
	"write.merge-mode",
	"history.expire.max-snapshot-age-ms",
	"history.expire.min-snapshots-to-keep",
	"gc.enabled",
	"format-version",
}

func tableProps(tbl *table.Table) propInfo {
	props := tbl.Properties()
	out := propInfo{icebergProperties: make(map[string]string)}
	for _, key := range trackedIcebergPropertyKeys {
		if value, ok := props[key]; ok {
			out.icebergProperties[key] = value
		}
	}
	for key, value := range props {
		if strings.HasPrefix(key, "write.parquet.bloom-filter-enabled.column.") {
			out.icebergProperties[key] = value
		}
	}
	if v, ok := props["write.distribution-mode"]; ok && v != "" {
		out.writeDistributionMode = &v
	}
	if v, ok := props["write.sort-order"]; ok && v != "" {
		t := true
		out.hasSortOrder = &t
	} else {
		t := false
		out.hasSortOrder = &t
	}
	if v, ok := props["write.target-file-size-bytes"]; ok && v != "" {
		if n, err := strconv.ParseInt(v, 10, 64); err == nil {
			out.targetFileSizeBytes = &n
		}
	}
	if v, ok := props["gc.enabled"]; ok {
		out.gcEnabled = parseBoolProperty(v)
	}
	return out
}

func parseBoolProperty(value string) *bool {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "true", "1", "yes":
		parsed := true
		return &parsed
	case "false", "0", "no":
		parsed := false
		return &parsed
	default:
		return nil
	}
}

func detectFormatVersion(tbl *table.Table) *int {
	props := tbl.Properties()
	for _, key := range []string{"format-version", "format.version"} {
		if v, ok := props[key]; ok && v != "" {
			if n, err := strconv.Atoi(v); err == nil {
				return &n
			}
		}
	}
	v := tbl.Metadata().Version()
	if v > 0 {
		return &v
	}
	return nil
}

func partitionFields(tbl *table.Table) []protocol.ScanStatsPartitionField {
	spec := tbl.Spec()
	schema := tbl.Schema()
	out := make([]protocol.ScanStatsPartitionField, 0, spec.NumFields())
	for i := 0; i < spec.NumFields(); i++ {
		field := spec.Field(i)
		transformRaw := field.Transform.String()
		baseColumn := field.Name
		colType := ""
		if schema != nil && len(field.SourceIDs) > 0 {
			if sf, ok := schema.FindFieldByID(field.SourceIDs[0]); ok {
				baseColumn = sf.Name
				colType = sf.Type.String()
			}
		}
		out = append(out, protocol.ScanStatsPartitionField{
			BaseColumn:     baseColumn,
			PartitionField: field.Name,
			Transform:      transformRaw,
			TransformParam: transformParam(transformRaw),
			ColType:        colType,
			FieldID:        field.FieldID,
		})
	}
	return out
}

// columnDefsFromSchema resolves every column's name/type by field ID from the table's
// full schema, not just its partition fields. Without this, addColumnStats' per-column
// accumulator (columns[id]) has no way to name/type a column on the live scan path --
// only AggregateFromFixture (Python<->Go parity tests) ever populated columnDefs, via a
// column list handed in by the test itself. Live scans looked up a.columnDefs[id], always
// missed, and fell back to name=strconv.Itoa(id) ("1", "10", "11"...) and colType="" --
// confirmed live, 2026-07-29: every column on a Go-engine-scanned table rendered its raw
// Iceberg field ID as the name and "unknown" as the type in the Structure tab, because
// nothing upstream of addColumnStats ever had the schema to resolve either from.
func columnDefsFromSchema(schema *iceberg.Schema) map[int]protocol.AggregateFixtureColumn {
	defs := map[int]protocol.AggregateFixtureColumn{}
	if schema == nil {
		return defs
	}
	fieldsIt, err := schema.FlatFields()
	if err != nil {
		return defs
	}
	for field := range fieldsIt {
		defs[field.ID] = protocol.AggregateFixtureColumn{
			FieldID:    field.ID,
			ColumnName: field.Name,
			ColumnType: field.Type.String(),
		}
	}
	return defs
}

func columnTypesFromSchema(schema *iceberg.Schema) map[int]iceberg.Type {
	types := map[int]iceberg.Type{}
	if schema == nil {
		return types
	}
	fieldsIt, err := schema.FlatFields()
	if err != nil {
		return types
	}
	for field := range fieldsIt {
		types[field.ID] = field.Type
	}
	return types
}

func transformParam(transformRaw string) *int {
	var digits strings.Builder
	found := false
	for _, ch := range transformRaw {
		if ch >= '0' && ch <= '9' {
			digits.WriteRune(ch)
			found = true
		} else if found {
			break
		}
	}
	if !found {
		return nil
	}
	n, err := strconv.Atoi(digits.String())
	if err != nil {
		return nil
	}
	return &n
}

type partAccum struct {
	values      map[string]any
	specID      *int32
	files       int
	small       int
	sizeBytes   int64
	deleteFiles int
}

type columnAccum struct {
	name       string
	colType    string
	nullCount  int64
	valueCount int64
}

type accumulator struct {
	fields              []protocol.ScanStatsPartitionField
	opts                Options
	partitions          map[string]*partAccum
	totalFiles          int64
	totalRecords        int64
	totalSizeBytes      int64
	healthSmallFiles    int64
	deleteFiles         int64
	equalityDeleteFiles int64
	positionDeleteFiles int64
	deleteBytes         int64
	fileFormats         map[string]struct{}
	manifestCount       int
	manifestTotalBytes  int64
	manifestErrors      []string
	columnDefs          map[int]protocol.AggregateFixtureColumn
	columns             map[int]*columnAccum
	columnTypes         map[int]iceberg.Type
	fileSamples         map[string]protocol.ScanStatsFileBoundSample
}

func newAccumulator(fields []protocol.ScanStatsPartitionField, opts Options) *accumulator {
	return &accumulator{
		fields:      fields,
		opts:        opts,
		partitions:  make(map[string]*partAccum),
		fileFormats: make(map[string]struct{}),
		columnDefs:  make(map[int]protocol.AggregateFixtureColumn),
		columns:     make(map[int]*columnAccum),
		columnTypes: make(map[int]iceberg.Type),
		fileSamples: make(map[string]protocol.ScanStatsFileBoundSample),
	}
}

func (a *accumulator) addRecord(rec FileRecord) {
	if rec.FileFormat != "" {
		a.fileFormats[strings.ToUpper(rec.FileFormat)] = struct{}{}
	}
	kind := deleteKind(rec.Content)
	if kind != "" {
		// Delete files (position/equality) are tracked here, in deleteFiles/deleteBytes/
		// *DeleteFiles -- they must NOT also fall into totalFiles/totalSizeBytes/totalRecords
		// or the per-partition files/small/sizeBytes tallies below. Regression, 2026-07-28:
		// a table with 34,176 position-delete files (each a few KB, so always "small") had
		// every one of them double-counted as a small DATA file too, inflating total_files,
		// small_files_count, files_per_partition, small_files_in_partition_count, and
		// partition_skew_ratio -- the planner then picked partitions for OPTIMIZE based on
		// delete-file counts, not actual small-data-file counts, and rewrite_data_files
		// (which doesn't touch delete files directly) predictably moved nothing.
		a.deleteFiles++
		a.deleteBytes += rec.SizeBytes
		if kind == "equality" {
			a.equalityDeleteFiles++
		} else {
			a.positionDeleteFiles++
		}
		// Still attribute the delete file to its partition's deleteFiles count (a distinct
		// counter from files/small, which stay data-file-only per the fix above). Without
		// this, convert_deletes' partition-skew scoping (planner.py's
		// _skewed_partition_predicates) has no way to find partitions where delete files are
		// concentrated but data files are already well-compacted -- confirmed live,
		// 2026-07-28: a table's 34,176 position-delete files sat in 4 partitions that, after
		// an earlier optimize pass, had only 5-7 data files each and so no longer looked
		// "skewed" by data-file count, silently starving them of convert_deletes forever.
		values := map[string]any{}
		for k, v := range rec.Partition {
			values[k] = jsonable(v)
		}
		key := partitionKey(values)
		acc := a.partitions[key]
		if acc == nil {
			specID := rec.SpecID
			acc = &partAccum{values: values, specID: &specID}
			a.partitions[key] = acc
		}
		acc.deleteFiles++
		return
	}
	a.totalFiles++
	a.totalSizeBytes += rec.SizeBytes
	a.totalRecords += rec.RecordCount
	if rec.SizeBytes < a.opts.HealthSmallFileBytes {
		a.healthSmallFiles++
	}
	a.addColumnStats(rec.NullValueCounts, rec.ValueCounts)
	values := map[string]any{}
	for k, v := range rec.Partition {
		values[k] = jsonable(v)
	}
	key := partitionKey(values)
	acc := a.partitions[key]
	if acc == nil {
		specID := rec.SpecID
		acc = &partAccum{values: values, specID: &specID}
		a.partitions[key] = acc
	}
	acc.files++
	acc.sizeBytes += rec.SizeBytes
	if float64(rec.SizeBytes) < a.opts.SmallFileMB*float64(mb) {
		acc.small++
	}
}

func (a *accumulator) addColumnStats(nulls, vals map[int]int64) {
	if nulls == nil {
		nulls = map[int]int64{}
	}
	if vals == nil {
		vals = map[int]int64{}
	}
	ids := map[int]struct{}{}
	for id := range nulls {
		ids[id] = struct{}{}
	}
	for id := range vals {
		ids[id] = struct{}{}
	}
	for id := range ids {
		col := a.columns[id]
		if col == nil {
			name := strconv.Itoa(id)
			colType := ""
			if def, ok := a.columnDefs[id]; ok {
				name = def.ColumnName
				colType = def.ColumnType
			}
			col = &columnAccum{name: name, colType: colType}
			a.columns[id] = col
		}
		col.nullCount += nulls[id]
		col.valueCount += vals[id]
	}
}

func (a *accumulator) addDataFile(df iceberg.DataFile, fields []protocol.ScanStatsPartitionField) {
	size := df.FileSizeBytes()
	if fmt := string(df.FileFormat()); fmt != "" {
		a.fileFormats[strings.ToUpper(fmt)] = struct{}{}
	}
	kind := deleteKindFromContent(df.ContentType())
	if kind != "" {
		// Delete files (position/equality) are tracked here, in deleteFiles/deleteBytes/
		// *DeleteFiles -- they must NOT also fall into totalFiles/totalSizeBytes/totalRecords
		// or the per-partition files/small/sizeBytes tallies below. Regression, 2026-07-28:
		// a table with 34,176 position-delete files (each a few KB, so always "small") had
		// every one of them double-counted as a small DATA file too, inflating total_files,
		// small_files_count, files_per_partition, small_files_in_partition_count, and
		// partition_skew_ratio -- the planner then picked partitions for OPTIMIZE based on
		// delete-file counts, not actual small-data-file counts, and rewrite_data_files
		// (which doesn't touch delete files directly) predictably moved nothing.
		a.deleteFiles++
		a.deleteBytes += size
		if kind == "equality" {
			a.equalityDeleteFiles++
		} else {
			a.positionDeleteFiles++
		}
		// Still attribute the delete file to its partition's deleteFiles count -- see the
		// matching comment in addRecord above for why convert_deletes' skew scoping needs
		// this. readDataFileMaps recovers from the same nil-partition-value panic as the
		// data-file path; only `values` is needed here, nulls/vals are discarded since
		// column stats never apply to delete-kind entries.
		_, _, values, note := readDataFileMaps(
			df.NullValueCounts,
			df.ValueCounts,
			func() map[string]any { return partitionValues(df, fields) },
		)
		if note != "" {
			a.noteManifestError(note)
		}
		key := partitionKey(values)
		acc := a.partitions[key]
		if acc == nil {
			specID := df.SpecID()
			acc = &partAccum{values: values, specID: &specID}
			a.partitions[key] = acc
		}
		acc.deleteFiles++
		return
	}
	a.totalFiles++
	a.totalSizeBytes += size
	a.totalRecords += df.Count()
	if size < a.opts.HealthSmallFileBytes {
		a.healthSmallFiles++
	}
	// NullValueCounts/ValueCounts/Partition all call iceberg-go initializeMapData,
	// which panics on nil date/timestamp partition values (v0.6.0). Recover per file
	// so totals still land; column_quality / partition key may be degraded for that file.
	nulls, vals, values, note := readDataFileMaps(
		df.NullValueCounts,
		df.ValueCounts,
		func() map[string]any { return partitionValues(df, fields) },
	)
	if note != "" {
		a.noteManifestError(note)
	}
	a.addColumnStats(nulls, vals)
	a.addFileSample(df)
	key := partitionKey(values)
	acc := a.partitions[key]
	if acc == nil {
		specID := df.SpecID()
		acc = &partAccum{values: values, specID: &specID}
		a.partitions[key] = acc
	}
	acc.files++
	acc.sizeBytes += size
	if float64(size) < a.opts.SmallFileMB*float64(mb) {
		acc.small++
	}
}

func (a *accumulator) addFileSample(df iceberg.DataFile) {
	if a.opts.MaxFileSamples <= 0 || df.FilePath() == "" {
		return
	}
	lower := decodeBounds(df.LowerBoundValues(), a.columnDefs, a.columnTypes)
	upper := decodeBounds(df.UpperBoundValues(), a.columnDefs, a.columnTypes)
	if len(lower) == 0 || len(upper) == 0 {
		return
	}
	a.fileSamples[df.FilePath()] = protocol.ScanStatsFileBoundSample{
		FilePath:    df.FilePath(),
		SizeBytes:   df.FileSizeBytes(),
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

func decodeBounds(
	bounds map[int][]byte,
	defs map[int]protocol.AggregateFixtureColumn,
	types map[int]iceberg.Type,
) map[string]any {
	result := map[string]any{}
	for id, encoded := range bounds {
		typ, ok := types[id]
		if !ok {
			continue
		}
		literal, err := iceberg.LiteralFromBytes(typ, encoded)
		if err != nil {
			continue
		}
		name := strconv.Itoa(id)
		if def, found := defs[id]; found {
			name = def.ColumnName
		}
		result[name] = jsonable(literal.Any())
	}
	return result
}

// readDataFileMaps loads column count maps and partition values, recovering from
// iceberg-go panics inside initializeMapData (nil logical-type partition values).
// On panic: returns nil column maps, empty partition values (_unpartitioned), and a note.
func readDataFileMaps(
	loadNulls func() map[int]int64,
	loadVals func() map[int]int64,
	loadPartition func() map[string]any,
) (nulls map[int]int64, vals map[int]int64, values map[string]any, note string) {
	defer func() {
		if r := recover(); r != nil {
			nulls, vals = nil, nil
			values = map[string]any{}
			note = fmt.Sprintf("iceberg-go map init panic (column/partition stats degraded): %v", r)
		}
	}()
	nulls = loadNulls()
	vals = loadVals()
	values = loadPartition()
	return
}

func (a *accumulator) noteManifestError(msg string) {
	for _, existing := range a.manifestErrors {
		if existing == msg {
			return
		}
	}
	a.manifestErrors = append(a.manifestErrors, msg)
}

func (a *accumulator) finish(schemaName, tableName string, meta *snapshotInfo) protocol.ScanStatsResponse {
	totalPartitions := len(a.partitions)
	var smallFilesCount int64
	filesPerPart := make([]int, 0, len(a.partitions))
	for _, acc := range a.partitions {
		filesPerPart = append(filesPerPart, acc.files)
		if acc.small > 1 && acc.files > 1 {
			smallFilesCount += int64(acc.small)
		}
	}
	maxFiles := 0
	sumFiles := 0
	for _, n := range filesPerPart {
		if n > maxFiles {
			maxFiles = n
		}
		sumFiles += n
	}
	avgFiles := 0.0
	skew := 0.0
	if len(filesPerPart) > 0 {
		avgFiles = math.Round((float64(sumFiles)/float64(len(filesPerPart)))*10) / 10
		med := medianFloat(filesPerPart)
		if med < 1 {
			med = 1
		}
		skew = math.Round((float64(maxFiles)/med)*10) / 10
	}

	parts := make([]protocol.ScanStatsPartitionAccum, 0, len(a.partitions))
	for key, acc := range a.partitions {
		partitionValue := key
		// Match Python partition_row: first field in partition_fields order.
		if len(a.fields) > 0 {
			name := a.fields[0].PartitionField
			if v, ok := acc.values[name]; ok {
				partitionValue = fmt.Sprint(v)
			}
		} else if len(acc.values) > 0 {
			names := make([]string, 0, len(acc.values))
			for name := range acc.values {
				names = append(names, name)
			}
			sort.Strings(names)
			partitionValue = fmt.Sprint(acc.values[names[0]])
		}
		parts = append(parts, protocol.ScanStatsPartitionAccum{
			PartitionKey:    key,
			PartitionValue:  partitionValue,
			PartitionValues: acc.values,
			PartitionSpecID: acc.specID,
			Files:           acc.files,
			Small:           acc.small,
			SizeBytes:       acc.sizeBytes,
			DeleteFiles:     acc.deleteFiles,
		})
	}
	sort.Slice(parts, func(i, j int) bool {
		return parts[i].PartitionKey < parts[j].PartitionKey
	})
	if len(parts) > a.opts.MaxPartitions {
		sort.Slice(parts, func(i, j int) bool {
			if parts[i].Small != parts[j].Small {
				return parts[i].Small > parts[j].Small
			}
			return parts[i].SizeBytes > parts[j].SizeBytes
		})
		parts = parts[:a.opts.MaxPartitions]
		sort.Slice(parts, func(i, j int) bool {
			return parts[i].PartitionKey < parts[j].PartitionKey
		})
	}
	formats := make([]string, 0, len(a.fileFormats))
	for f := range a.fileFormats {
		formats = append(formats, f)
	}
	sort.Strings(formats)
	if a.fields == nil {
		a.fields = []protocol.ScanStatsPartitionField{}
	}
	if a.manifestErrors == nil {
		a.manifestErrors = []string{}
	}

	columnQuality := make([]protocol.ScanStatsColumnQuality, 0, len(a.columns))
	ids := make([]int, 0, len(a.columns))
	for id := range a.columns {
		ids = append(ids, id)
	}
	sort.Ints(ids)
	var sumRate, maxRate float64
	var worst *string
	rated := 0
	for _, id := range ids {
		col := a.columns[id]
		rate := 0.0
		rounded := 0.0
		if col.valueCount > 0 {
			rate = float64(col.nullCount) / float64(col.valueCount)
			rounded = round4(rate)
			sumRate += rounded
			rated++
			if rounded > 0 && (worst == nil || rounded > maxRate) {
				maxRate = rounded
				name := col.name
				worst = &name
			}
		}
		columnQuality = append(columnQuality, protocol.ScanStatsColumnQuality{
			FieldID:    id,
			ColumnName: col.name,
			ColumnType: col.colType,
			NullCount:  col.nullCount,
			ValueCount: col.valueCount,
			NullRate:   rounded,
		})
	}
	var avgNull, maxNull *float64
	if rated > 0 {
		avg := round4(sumRate / float64(rated))
		maxv := round4(maxRate)
		avgNull = &avg
		maxNull = &maxv
	} else if len(columnQuality) == 0 {
		zero := 0.0
		avgNull = &zero
		maxNull = &zero
		worst = nil
	}

	resp := protocol.ScanStatsResponse{
		OK:                   true,
		SchemaName:           schemaName,
		TableName:            tableName,
		TotalFiles:           a.totalFiles,
		TotalRecords:         a.totalRecords,
		TotalSizeBytes:       a.totalSizeBytes,
		HealthSmallFiles:     a.healthSmallFiles,
		SmallFilesCount:      smallFilesCount,
		DeleteFiles:          a.deleteFiles,
		EqualityDeleteFiles:  a.equalityDeleteFiles,
		PositionDeleteFiles:  a.positionDeleteFiles,
		DeleteBytes:          a.deleteBytes,
		ManifestCount:        a.manifestCount,
		ManifestTotalBytes:   a.manifestTotalBytes,
		TotalPartitions:      totalPartitions,
		MaxFilesPerPartition: maxFiles,
		AvgFilesPerPartition: avgFiles,
		PartitionSkewRatio:   skew,
		FileFormats:          formats,
		PartitionFields:      a.fields,
		Partitions:           parts,
		ManifestReadErrors:   a.manifestErrors,
		ColumnQuality:        columnQuality,
		AvgNullRate:          avgNull,
		MaxNullRate:          maxNull,
		WorstNullColumn:      worst,
	}
	samplePaths := make([]string, 0, len(a.fileSamples))
	for path := range a.fileSamples {
		samplePaths = append(samplePaths, path)
	}
	sort.Strings(samplePaths)
	for _, path := range samplePaths {
		resp.WairFileSample = append(resp.WairFileSample, a.fileSamples[path])
	}
	resp.WairFileSampleComplete = int64(len(a.fileSamples)) == a.totalFiles
	if meta != nil {
		resp.TotalSnapshots = meta.totalSnapshots
		resp.FirstSnapshotAt = meta.firstSnapshotAt
		resp.LastSnapshotAt = meta.lastSnapshotAt
		resp.CurrentSnapshotID = meta.currentSnapshotID
	}
	return resp
}

func round4(v float64) float64 {
	return math.Round(v*10000) / 10000
}

func medianFloat(values []int) float64 {
	if len(values) == 0 {
		return 0
	}
	cp := append([]int(nil), values...)
	sort.Ints(cp)
	mid := len(cp) / 2
	if len(cp)%2 == 1 {
		return float64(cp[mid])
	}
	return float64(cp[mid-1]+cp[mid]) / 2
}

func partitionValues(df iceberg.DataFile, fields []protocol.ScanStatsPartitionField) map[string]any {
	raw := df.Partition()
	values := make(map[string]any, len(fields))
	for _, field := range fields {
		name := field.PartitionField
		if field.FieldID != 0 {
			if val, ok := raw[field.FieldID]; ok {
				values[name] = jsonable(val)
				continue
			}
		}
		values[name] = nil
	}
	return values
}

func partitionKey(values map[string]any) string {
	if len(values) == 0 {
		return "_unpartitioned"
	}
	keys := make([]string, 0, len(values))
	for k := range values {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, fmt.Sprintf("%s=%v", k, values[k]))
	}
	return strings.Join(parts, "|")
}

func deleteKind(content string) string {
	c := strings.ToLower(strings.TrimSpace(content))
	if strings.Contains(c, "equality") || c == "2" {
		return "equality"
	}
	if strings.Contains(c, "position") || strings.HasSuffix(c, "deletes") || c == "1" {
		return "position"
	}
	return ""
}

func deleteKindFromContent(content iceberg.ManifestEntryContent) string {
	switch content {
	case iceberg.EntryContentEqDeletes:
		return "equality"
	case iceberg.EntryContentPosDeletes:
		return "position"
	default:
		return ""
	}
}

func jsonable(value any) any {
	switch v := value.(type) {
	case nil:
		return nil
	case string, bool:
		return v
	case int:
		return v
	case int8:
		return int(v)
	case int16:
		return int(v)
	case int32:
		return int(v)
	case int64:
		return v
	case float32:
		if math.IsNaN(float64(v)) || math.IsInf(float64(v), 0) {
			return nil
		}
		return float64(v)
	case float64:
		if math.IsNaN(v) || math.IsInf(v, 0) {
			return nil
		}
		return v
	case time.Time:
		return v.UTC().Format(time.RFC3339Nano)
	default:
		return fmt.Sprint(v)
	}
}

func splitIdent(ident table.Identifier) (schema, name string) {
	if len(ident) == 0 {
		return "", ""
	}
	if len(ident) == 1 {
		return "", ident[0]
	}
	return strings.Join(ident[:len(ident)-1], "."), ident[len(ident)-1]
}
