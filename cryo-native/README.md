# Cryo Native Engine

High-performance Iceberg table compaction engine written in Rust with Python bindings.

## Features

- **Binpack Compaction**: Merge small files without sorting
- **Sort Compaction**: Merge and sort files by specified columns
- **Z-Order Clustering**: Multi-column clustering for improved pruning
- **High Performance**: Uses Apache DataFusion and Arrow for columnar processing

## Installation

### From Source

Requires Rust 1.75+ and Python 3.10+.

```bash
# Install maturin
pip install maturin

# Build and install
cd cryo-native
maturin develop --release
```

### From PyPI (when published)

```bash
pip install cryo-native
```

## Usage

```python
from cryo_native import PyCompactionEngine, PyCompactionConfig

# Create engine
engine = PyCompactionEngine()

# Configure compaction
config = PyCompactionConfig(
    target_file_size_bytes=256 * 1024 * 1024,  # 256 MB
    min_file_size_bytes=64 * 1024 * 1024,       # 64 MB
)

# Binpack files (merge without sorting)
result = engine.binpack_files(
    input_paths=["file1.parquet", "file2.parquet"],
    output_dir="/output",
    config=config,
)
print(f"Compacted {result.input_files} files into {result.output_files} files")

# Sort files
result = engine.sort_files(
    input_paths=["file1.parquet", "file2.parquet"],
    output_dir="/output",
    sort_columns=["date", "user_id"],
    config=config,
)

# Z-order clustering
result = engine.zorder_files(
    input_paths=["file1.parquet", "file2.parquet"],
    output_dir="/output",
    zorder_columns=["region", "category", "date"],
    config=config,
)

# Analyze files
analysis = engine.analyze_files(["file1.parquet", "file2.parquet"])
print(f"Recommended strategy: {analysis['recommended_strategy']}")
```

## Performance

The native engine typically provides 2-5x performance improvement over JVM-based
compaction for small to medium file operations. Benefits include:

- No JVM startup overhead
- Efficient memory usage with Arrow's columnar format
- Native SIMD operations via DataFusion
- Reduced network overhead for local operations

## License

Apache License 2.0
