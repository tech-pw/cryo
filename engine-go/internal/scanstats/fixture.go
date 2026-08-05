package scanstats

import (
	"strconv"

	"github.com/cryo-lake/cryo/engine-go/internal/protocol"
)

// AggregateFromFixture runs the same aggregation path as AggregateFromRecords but
// accepts full table metadata so Python↔Go parity tests can compare complete
// TableAnalysis outputs without a live catalog.
func AggregateFromFixture(req *protocol.AggregateFixtureRequest) protocol.ScanStatsResponse {
	if req == nil {
		err := "nil aggregate-fixture request"
		return protocol.ScanStatsResponse{OK: false, Error: &err}
	}
	opts := Options{
		SmallFileMB:          req.SmallFileMB,
		HealthSmallFileBytes: req.HealthSmallFileBytes,
		MaxPartitions:        req.MaxPartitions,
	}.withDefaults()

	columns := make(map[int]protocol.AggregateFixtureColumn, len(req.Columns))
	for _, col := range req.Columns {
		columns[col.FieldID] = col
	}

	acc := newAccumulator(req.PartitionFields, opts)
	acc.columnDefs = columns
	for _, rec := range req.Records {
		nulls := map[int]int64{}
		vals := map[int]int64{}
		for k, v := range rec.NullValueCounts {
			if id, err := strconv.Atoi(k); err == nil {
				nulls[id] = v
			}
		}
		for k, v := range rec.ValueCounts {
			if id, err := strconv.Atoi(k); err == nil {
				vals[id] = v
			}
		}
		acc.addRecord(FileRecord{
			SizeBytes:       rec.SizeBytes,
			RecordCount:     rec.RecordCount,
			Content:         rec.Content,
			FileFormat:      rec.FileFormat,
			SpecID:          rec.SpecID,
			Partition:       rec.Partition,
			NullValueCounts: nulls,
			ValueCounts:     vals,
		})
	}
	acc.manifestCount = req.ManifestCount
	acc.manifestTotalBytes = req.ManifestTotalBytes

	meta := &snapshotInfo{
		totalSnapshots:    req.TotalSnapshots,
		firstSnapshotAt:   req.FirstSnapshotAt,
		lastSnapshotAt:    req.LastSnapshotAt,
		currentSnapshotID: req.CurrentSnapshotID,
	}
	resp := acc.finish(req.SchemaName, req.TableName, meta)
	resp.OK = true
	resp.Location = req.Location
	resp.WriteDistributionMode = req.WriteDistributionMode
	resp.HasSortOrder = req.HasSortOrder
	resp.TargetFileSizeBytes = req.TargetFileSizeBytes
	resp.IcebergProperties = req.IcebergProperties
	resp.GCEnabled = req.GCEnabled
	resp.FormatVersion = req.FormatVersion
	return resp
}
