package ops

import (
	"context"
	"os"
	"strings"
	"testing"

	"github.com/apache/arrow-go/v18/arrow"
	"github.com/apache/arrow-go/v18/arrow/array"
	"github.com/apache/arrow-go/v18/arrow/memory"
	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/catalog/hadoop"
	"github.com/apache/iceberg-go/table"
)

var cleanupTestArrowSchema = arrow.NewSchema([]arrow.Field{
	{Name: "id", Type: arrow.PrimitiveTypes.Int64, Nullable: false},
	{Name: "label", Type: arrow.BinaryTypes.String, Nullable: false},
}, nil)

func newCleanupTestTable(t *testing.T) (*hadoop.Catalog, table.Identifier, *table.Table) {
	t.Helper()
	ctx := context.Background()
	cat, err := hadoop.NewCatalog("test", t.TempDir(), iceberg.Properties{})
	if err != nil {
		t.Fatalf("new catalog: %v", err)
	}
	if err := cat.CreateNamespace(ctx, table.Identifier{"ns"}, nil); err != nil {
		t.Fatalf("create namespace: %v", err)
	}
	schema := iceberg.NewSchema(0,
		iceberg.NestedField{ID: 1, Name: "id", Type: iceberg.PrimitiveTypes.Int64, Required: true},
		iceberg.NestedField{ID: 2, Name: "label", Type: iceberg.PrimitiveTypes.String, Required: true},
	)
	ident := table.Identifier{"ns", "events"}
	tbl, err := cat.CreateTable(ctx, ident, schema)
	if err != nil {
		t.Fatalf("create table: %v", err)
	}
	return cat, ident, tbl
}

func cleanupTestBatch(t *testing.T, rows string) arrow.Table {
	t.Helper()
	tbl, err := array.TableFromJSON(memory.DefaultAllocator, cleanupTestArrowSchema, []string{rows})
	if err != nil {
		t.Fatalf("arrow table: %v", err)
	}
	t.Cleanup(tbl.Release)
	return tbl
}

func appendBatch(t *testing.T, tbl *table.Table, rows string) *table.Table {
	t.Helper()
	txn := tbl.NewTransaction()
	if err := txn.AppendTable(context.Background(), cleanupTestBatch(t, rows), 1024, nil); err != nil {
		t.Fatalf("append: %v", err)
	}
	updated, err := txn.Commit(context.Background())
	if err != nil {
		t.Fatalf("commit append: %v", err)
	}
	return updated
}

func overwriteBatch(t *testing.T, tbl *table.Table, rows string) *table.Table {
	t.Helper()
	txn := tbl.NewTransaction()
	if err := txn.OverwriteTable(context.Background(), cleanupTestBatch(t, rows), 1024, nil); err != nil {
		t.Fatalf("overwrite: %v", err)
	}
	updated, err := txn.Commit(context.Background())
	if err != nil {
		t.Fatalf("commit overwrite: %v", err)
	}
	return updated
}

func expireAllButCurrent(t *testing.T, tbl *table.Table) *table.Table {
	t.Helper()
	txn := tbl.NewTransaction()
	if err := txn.ExpireSnapshots(expireOptions(0, 1)...); err != nil {
		t.Fatalf("expire: %v", err)
	}
	updated, err := txn.Commit(context.Background())
	if err != nil {
		t.Fatalf("commit expire: %v", err)
	}
	if got := len(updated.Metadata().Snapshots()); got != 1 {
		t.Fatalf("expected 1 retained snapshot, got %d", got)
	}
	return updated
}

// snapshotFiles returns the manifest list, manifests and live content files of one snapshot.
func snapshotFiles(t *testing.T, tbl *table.Table, snap table.Snapshot) (string, []string, []string) {
	t.Helper()
	fs, err := tbl.FS(context.Background())
	if err != nil {
		t.Fatalf("fs: %v", err)
	}
	manifests, err := snap.Manifests(fs)
	if err != nil {
		t.Fatalf("manifests: %v", err)
	}
	var manifestPaths, dataPaths []string
	for _, manifest := range manifests {
		manifestPaths = append(manifestPaths, manifest.FilePath())
		for entry, err := range manifest.Entries(fs, true) {
			if err != nil {
				t.Fatalf("entries: %v", err)
			}
			dataPaths = append(dataPaths, entry.DataFile().FilePath())
		}
	}
	return snap.ManifestList, manifestPaths, dataPaths
}

func fileExists(t *testing.T, path string) bool {
	t.Helper()
	_, err := os.Stat(strings.TrimPrefix(path, "file://"))
	if err == nil {
		return true
	}
	if os.IsNotExist(err) {
		return false
	}
	t.Fatalf("stat %s: %v", path, err)
	return false
}

func TestDeleteExpiredSnapshotFilesRemovesFilesOnlyExpiredSnapshotsReferenced(t *testing.T) {
	_, _, tbl := newCleanupTestTable(t)
	tbl = appendBatch(t, tbl, `[{"id": 1, "label": "a"}]`)
	tbl = appendBatch(t, tbl, `[{"id": 2, "label": "b"}]`)

	var expiredLists, expiredManifests, expiredData []string
	seenData := make(map[string]struct{})
	for _, snap := range tbl.Metadata().Snapshots() {
		list, manifests, data := snapshotFiles(t, tbl, snap)
		expiredLists = append(expiredLists, list)
		expiredManifests = append(expiredManifests, manifests...)
		for _, path := range data {
			if _, ok := seenData[path]; !ok {
				seenData[path] = struct{}{}
				expiredData = append(expiredData, path)
			}
		}
	}

	tbl = overwriteBatch(t, tbl, `[{"id": 3, "label": "c"}]`)
	before := tbl
	after := expireAllButCurrent(t, before)
	keptList, keptManifests, keptData := snapshotFiles(t, after, *after.CurrentSnapshot())

	cleanup, err := deleteExpiredSnapshotFiles(context.Background(), before, after, 4)
	if err != nil {
		t.Fatalf("cleanup: %v", err)
	}
	if cleanup.deleted == 0 || cleanup.deleted != cleanup.candidates {
		t.Fatalf("expected every candidate deleted, got %+v", cleanup)
	}
	if cleanup.bytes == 0 {
		t.Fatalf("expected deleted bytes to be reported, got %+v", cleanup)
	}

	kept := make(map[string]struct{})
	for _, path := range append(append([]string{keptList}, keptManifests...), keptData...) {
		kept[path] = struct{}{}
		if !fileExists(t, path) {
			t.Errorf("file referenced by the retained snapshot was deleted: %s", path)
		}
	}
	if len(expiredData) != 2 {
		t.Fatalf("expected 2 data files from the overwritten appends, got %d", len(expiredData))
	}
	for _, path := range expiredData {
		if _, ok := kept[path]; ok {
			t.Fatalf("overwritten data file is still live: %s", path)
		}
	}
	for _, path := range append(append(expiredLists, expiredManifests...), expiredData...) {
		if _, ok := kept[path]; ok {
			continue
		}
		if fileExists(t, path) {
			t.Errorf("file only referenced by expired snapshots still exists: %s", path)
		}
	}

	rows, err := after.Scan().ToArrowTable(context.Background())
	if err != nil {
		t.Fatalf("scan after cleanup: %v", err)
	}
	defer rows.Release()
	if rows.NumRows() != 1 {
		t.Fatalf("expected the overwritten row only, got %d rows", rows.NumRows())
	}
}

func TestDeleteExpiredSnapshotFilesKeepsFilesStillLiveInRetainedSnapshot(t *testing.T) {
	_, _, tbl := newCleanupTestTable(t)
	tbl = appendBatch(t, tbl, `[{"id": 1, "label": "a"}]`)
	firstList, _, _ := snapshotFiles(t, tbl, *tbl.CurrentSnapshot())
	tbl = appendBatch(t, tbl, `[{"id": 2, "label": "b"}]`)

	before := tbl
	after := expireAllButCurrent(t, before)
	keptList, keptManifests, keptData := snapshotFiles(t, after, *after.CurrentSnapshot())
	if len(keptData) != 2 {
		t.Fatalf("expected both appended files live in the retained snapshot, got %d", len(keptData))
	}

	cleanup, err := deleteExpiredSnapshotFiles(context.Background(), before, after, 4)
	if err != nil {
		t.Fatalf("cleanup: %v", err)
	}
	if cleanup.deleted != 1 {
		t.Fatalf("expected only the expired manifest list deleted, got %+v", cleanup)
	}
	if fileExists(t, firstList) {
		t.Errorf("expired manifest list still exists: %s", firstList)
	}
	for _, path := range append(append([]string{keptList}, keptManifests...), keptData...) {
		if !fileExists(t, path) {
			t.Errorf("file referenced by the retained snapshot was deleted: %s", path)
		}
	}
}

func TestDeleteExpiredSnapshotFilesNoopWithoutExpiredSnapshots(t *testing.T) {
	_, _, tbl := newCleanupTestTable(t)
	tbl = appendBatch(t, tbl, `[{"id": 1, "label": "a"}]`)

	cleanup, err := deleteExpiredSnapshotFiles(context.Background(), tbl, tbl, 4)
	if err != nil {
		t.Fatalf("cleanup: %v", err)
	}
	if cleanup != (expiredFileCleanup{}) {
		t.Fatalf("expected no cleanup, got %+v", cleanup)
	}
}

func TestRemoveFilesTreatsMissingFilesAsAlreadyDeleted(t *testing.T) {
	_, _, tbl := newCleanupTestTable(t)
	fs, err := tbl.FS(context.Background())
	if err != nil {
		t.Fatalf("fs: %v", err)
	}
	present := t.TempDir() + "/present.parquet"
	if err := os.WriteFile(present, []byte("1234"), 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}
	cleanup, err := removeFiles(context.Background(), fs, map[string]int64{
		present:                          4,
		t.TempDir() + "/missing.parquet": 8,
	}, 2)
	if err != nil {
		t.Fatalf("remove: %v", err)
	}
	if cleanup.deleted != 1 || cleanup.bytes != 4 || cleanup.candidates != 2 {
		t.Fatalf("unexpected cleanup %+v", cleanup)
	}
	if fileExists(t, present) {
		t.Fatal("present file was not deleted")
	}
}
