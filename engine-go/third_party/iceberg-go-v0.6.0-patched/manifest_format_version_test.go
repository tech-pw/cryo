package iceberg

import (
	"bytes"
	"io"
	"testing"

	"github.com/stretchr/testify/require"
)

// A v2 table upgraded to v3 keeps its old manifests. The v3 manifest list entry
// advertises version 3 while the file's own format-version is 2. Readers must
// decode the file as v2. A file newer than the list is still rejected (#421).
func TestManifestReaderAcceptsOlderFormatThanTheListEntry(t *testing.T) {
	body := writeOneFileManifest(t, 2)

	reader, err := NewManifestReader(listEntry(3), bytes.NewReader(body))
	require.NoError(t, err)
	t.Cleanup(func() { _ = reader.Close() })
	require.Equal(t, 2, reader.Version())

	entry, err := reader.ReadEntry()
	require.NoError(t, err)
	require.Equal(t, int64(4096), entry.DataFile().FileSizeBytes())

	_, err = reader.ReadEntry()
	require.ErrorIs(t, err, io.EOF)
}

func TestManifestReaderStillAcceptsAMatchingFormatVersion(t *testing.T) {
	body := writeOneFileManifest(t, 2)
	reader, err := NewManifestReader(listEntry(2), bytes.NewReader(body))
	require.NoError(t, err)
	t.Cleanup(func() { _ = reader.Close() })
	_, err = reader.ReadEntry()
	require.NoError(t, err)
}

func TestManifestReaderRejectsAFormatNewerThanTheListEntry(t *testing.T) {
	body := writeOneFileManifest(t, 3)
	_, err := NewManifestReader(listEntry(2), bytes.NewReader(body))
	require.Error(t, err)
	require.Contains(t, err.Error(), "indicates version 3")
	require.Contains(t, err.Error(), "indicates version 2")
}

func writeOneFileManifest(t *testing.T, version int) []byte {
	t.Helper()
	schema := NewSchema(0, NestedField{Type: Int64Type{}, ID: 1, Name: "id", Required: true})
	spec := NewPartitionSpec()
	var buf bytes.Buffer
	writer, err := NewManifestWriter(version, &buf, spec, schema, 1)
	require.NoError(t, err)
	snapshot := int64(7)
	seq := int64(1)
	dataFile, err := NewDataFileBuilder(
		spec,
		EntryContentData,
		"s3://bucket/data.parquet",
		ParquetFile,
		nil,
		nil,
		nil,
		10,
		4096,
	)
	require.NoError(t, err)
	require.NoError(t, writer.Add(NewManifestEntry(EntryStatusADDED, &snapshot, &seq, &seq, dataFile.Build())))
	require.NoError(t, writer.Close())
	return buf.Bytes()
}

func listEntry(version int) ManifestFile {
	return &manifestFile{version: version, Content: ManifestContentData}
}
