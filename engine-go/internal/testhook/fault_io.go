package testhook

import (
	"context"
	"fmt"
	"io/fs"
	"strings"
	"sync/atomic"

	iceio "github.com/apache/iceberg-go/io"
)

var dataPutCount atomic.Uint64

func isDataObjectPath(path string) bool {
	lower := strings.ToLower(path)
	return strings.Contains(lower, "/data/") && strings.HasSuffix(lower, ".parquet")
}

func shouldFailDataPut(path string) error {
	target := FailDataPutOn()
	if target <= 0 || !isDataObjectPath(path) {
		return nil
	}
	n := dataPutCount.Add(1)
	if int(n) != target {
		return nil
	}
	return fmt.Errorf("injected object-store PUT failure for %s (write #%d)", path, n)
}

type faultWriteIO struct {
	inner iceio.IO
}

// WrapWriteIO returns a WriteFileIO that can inject PUT failures for tests.
func WrapWriteIO(base iceio.IO) iceio.IO {
	if FailDataPutOn() <= 0 {
		return base
	}
	w, ok := base.(iceio.WriteFileIO)
	if !ok {
		return base
	}
	return &faultWriteIO{inner: w}
}

func (f *faultWriteIO) Open(name string) (iceio.File, error) {
	return f.inner.Open(name)
}

func (f *faultWriteIO) Remove(name string) error {
	return f.inner.Remove(name)
}

func (f *faultWriteIO) Create(name string) (iceio.FileWriter, error) {
	if err := shouldFailDataPut(name); err != nil {
		return nil, err
	}
	w := f.inner.(iceio.WriteFileIO)
	return w.Create(name)
}

func (f *faultWriteIO) WriteFile(name string, p []byte) error {
	if err := shouldFailDataPut(name); err != nil {
		return err
	}
	w := f.inner.(iceio.WriteFileIO)
	return w.WriteFile(name, p)
}

func (f *faultWriteIO) ReadFile(name string) ([]byte, error) {
	r, ok := f.inner.(iceio.ReadFileIO)
	if !ok {
		return nil, fmt.Errorf("underlying IO does not implement ReadFileIO")
	}
	return r.ReadFile(name)
}

func (f *faultWriteIO) WalkDir(root string, fn fs.WalkDirFunc) error {
	l, ok := f.inner.(iceio.ListableIO)
	if !ok {
		return fmt.Errorf("underlying IO does not implement ListableIO")
	}
	return l.WalkDir(root, fn)
}

func (f *faultWriteIO) DeleteFiles(ctx context.Context, paths []string) ([]string, error) {
	b, ok := f.inner.(iceio.BulkRemovableIO)
	if !ok {
		return nil, fmt.Errorf("underlying IO does not implement BulkRemovableIO")
	}
	return b.DeleteFiles(ctx, paths)
}
