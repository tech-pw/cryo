package fixtures

import (
	"context"
	"fmt"
	"strings"

	"github.com/apache/arrow-go/v18/arrow"
	"github.com/apache/arrow-go/v18/arrow/array"
	"github.com/apache/arrow-go/v18/arrow/memory"
	"github.com/apache/iceberg-go"
	iceio "github.com/apache/iceberg-go/io"
	"github.com/apache/iceberg-go/table"
	"github.com/apache/arrow-go/v18/parquet"
	"github.com/apache/arrow-go/v18/parquet/pqarrow"
)

func appendOneRow(ctx context.Context, tbl *table.Table, id int64, label string) (*table.Table, error) {
	arrowSchema, err := table.SchemaToArrowSchema(tbl.Schema(), nil, false, false)
	if err != nil {
		return nil, err
	}
	payload := fmt.Sprintf(`[{"id": %d, "label": %q}]`, id, label)
	rec, _, err := array.RecordFromJSON(memory.DefaultAllocator, arrowSchema, strings.NewReader(payload))
	if err != nil {
		return nil, err
	}
	defer rec.Release()

	reader, err := array.NewRecordReader(arrowSchema, []arrow.RecordBatch{rec})
	if err != nil {
		return nil, err
	}
	defer reader.Release()

	txn := tbl.NewTransaction()
	if err := txn.Append(ctx, reader, nil); err != nil {
		return nil, err
	}
	return txn.Commit(ctx)
}

func appendManySmallFiles(ctx context.Context, tbl *table.Table, count int) (*table.Table, map[int64]string, error) {
	idToPath := make(map[int64]string, count)
	var err error
	for i := range count {
		tbl, err = appendOneRow(ctx, tbl, int64(i), fmt.Sprintf("row-%d", i))
		if err != nil {
			return nil, nil, fmt.Errorf("append row %d: %w", i, err)
		}
		paths, pathErr := dataFilePaths(ctx, tbl)
		if pathErr != nil {
			return nil, nil, pathErr
		}
		if len(paths) == 0 {
			return nil, nil, fmt.Errorf("expected data file after append row %d", i)
		}
		idToPath[int64(i)] = paths[len(paths)-1]
	}
	return tbl, idToPath, nil
}

func commitPositionDeletes(
	ctx context.Context,
	tbl *table.Table,
	dataPath string,
	positions []int64,
) (*table.Table, error) {
	fs, err := tbl.FS(ctx)
	if err != nil {
		return nil, err
	}
	writeFS, ok := fs.(iceio.WriteFileIO)
	if !ok {
		return nil, fmt.Errorf("table filesystem does not support writes")
	}

	posDelPath := tbl.Location() + fmt.Sprintf("/data/pos-del-%d.parquet", len(positions))
	entries := make([]string, 0, len(positions))
	for _, pos := range positions {
		entries = append(entries, fmt.Sprintf(`{"file_path": %q, "pos": %d}`, dataPath, pos))
	}
	jsonPayload := "[" + strings.Join(entries, ", ") + "]"

	if err := writeParquetFile(ctx, writeFS, posDelPath, table.PositionalDeleteArrowSchema, jsonPayload); err != nil {
		return nil, err
	}

	builder, err := iceberg.NewDataFileBuilder(
		*iceberg.UnpartitionedSpec,
		iceberg.EntryContentPosDeletes,
		posDelPath,
		iceberg.ParquetFile,
		nil,
		nil,
		nil,
		1,
		128,
	)
	if err != nil {
		return nil, err
	}
	ref := dataPath
	builder = builder.ReferencedDataFile(ref)

	txn := tbl.NewTransaction()
	rd := txn.NewRowDelta(nil)
	rd.AddDeletes(builder.Build())
	if err := rd.Commit(ctx); err != nil {
		return nil, err
	}
	return txn.Commit(ctx)
}

func commitEqualityDelete(ctx context.Context, tbl *table.Table, ids []int64) (*table.Table, error) {
	if len(ids) == 0 {
		return tbl, nil
	}
	entries := make([]string, 0, len(ids))
	for _, id := range ids {
		entries = append(entries, fmt.Sprintf(`{"id": %d}`, id))
	}
	jsonPayload := "[" + strings.Join(entries, ", ") + "]"

	delFields := make([]iceberg.NestedField, 0, 1)
	field, ok := tbl.Schema().FindFieldByID(1)
	if !ok {
		return nil, fmt.Errorf("schema field id=1 not found")
	}
	delFields = append(delFields, field)
	delSchema := iceberg.NewSchema(0, delFields...)
	delArrowSchema, err := table.SchemaToArrowSchema(delSchema, nil, true, false)
	if err != nil {
		return nil, err
	}
	rec, _, err := array.RecordFromJSON(memory.DefaultAllocator, delArrowSchema, strings.NewReader(jsonPayload))
	if err != nil {
		return nil, err
	}
	defer rec.Release()

	records := func(yield func(arrow.RecordBatch, error) bool) {
		yield(rec, nil)
	}

	txn := tbl.NewTransaction()
	deleteFiles, err := txn.WriteEqualityDeletes(ctx, []int{1}, records)
	if err != nil {
		return nil, err
	}
	rd := txn.NewRowDelta(nil)
	for _, df := range deleteFiles {
		rd.AddDeletes(df)
	}
	if err := rd.Commit(ctx); err != nil {
		return nil, err
	}
	return txn.Commit(ctx)
}

func writeParquetFile(
	ctx context.Context,
	fs iceio.WriteFileIO,
	path string,
	schema *arrow.Schema,
	jsonData string,
) error {
	rec, _, err := array.RecordFromJSON(memory.DefaultAllocator, schema, strings.NewReader(jsonData))
	if err != nil {
		return err
	}
	defer rec.Release()

	fw, err := fs.Create(path)
	if err != nil {
		return err
	}
	defer func() { _ = fw.Close() }()

	arrowTable := array.NewTableFromRecords(schema, []arrow.RecordBatch{rec})
	defer arrowTable.Release()

	return pqarrow.WriteTable(
		arrowTable,
		fw,
		rec.NumRows(),
		parquet.NewWriterProperties(parquet.WithStats(true)),
		pqarrow.DefaultWriterProps(),
	)
}
