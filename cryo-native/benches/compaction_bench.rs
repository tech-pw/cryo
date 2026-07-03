use criterion::{criterion_group, criterion_main, BenchmarkId, Criterion, Throughput};
use std::path::PathBuf;
use tempfile::TempDir;

fn bench_binpack(c: &mut Criterion) {
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();

    let mut group = c.benchmark_group("compaction");

    // Benchmark with different file counts
    for file_count in [10usize, 50, 100] {
        let tmp = TempDir::new().unwrap();
        let input_paths = create_test_parquet_files(&rt, tmp.path(), file_count, 1_000);
        let total_rows = file_count * 1_000;
        group.throughput(Throughput::Elements(total_rows as u64));

        let output_dir = tmp.path().join("output");
        std::fs::create_dir_all(&output_dir).unwrap();

        let config = cryo_native::compaction::CompactionConfig::default();

        group.bench_with_input(
            BenchmarkId::new("binpack", file_count),
            &(&input_paths, output_dir.to_str().unwrap().to_string()),
            |b, (paths, out)| {
                b.iter(|| {
                    rt.block_on(async {
                        cryo_native::compaction::binpack_files(paths, out, &config)
                            .await
                            .unwrap()
                    })
                });
            },
        );
    }

    group.finish();
}

fn bench_sort(c: &mut Criterion) {
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();

    let mut group = c.benchmark_group("sort_compaction");
    group.sample_size(10);  // Fewer samples for heavier operations

    let tmp = TempDir::new().unwrap();
    let input_paths = create_test_parquet_files(&rt, tmp.path(), 20, 5_000);
    let output_dir = tmp.path().join("sort_output");
    std::fs::create_dir_all(&output_dir).unwrap();

    let mut config = cryo_native::compaction::CompactionConfig::default();
    config.sort_columns = vec!["event_date".to_string(), "user_id".to_string()];

    group.bench_function("sort_20files_100k_rows", |b| {
        b.iter(|| {
            rt.block_on(async {
                cryo_native::compaction::sort_files(
                    &input_paths,
                    output_dir.to_str().unwrap(),
                    &config,
                )
                .await
                .unwrap()
            })
        });
    });

    group.finish();
}

fn create_test_parquet_files(
    rt: &tokio::runtime::Runtime,
    dir: &std::path::Path,
    count: usize,
    rows_per_file: usize,
) -> Vec<String> {
    use arrow::array::{Int64Array, StringArray, Int32Array};
    use arrow::datatypes::{DataType, Field, Schema};
    use arrow::record_batch::RecordBatch;
    use parquet::arrow::ArrowWriter;
    use std::sync::Arc;

    let schema = Arc::new(Schema::new(vec![
        Field::new("user_id", DataType::Int64, false),
        Field::new("event_date", DataType::Utf8, false),
        Field::new("value", DataType::Int32, false),
    ]));

    let mut paths = Vec::new();

    for i in 0..count {
        let path = dir.join(format!("test_{:04}.parquet", i));

        let user_ids: Vec<i64> = (0..rows_per_file as i64).map(|j| i as i64 * 1000 + j).collect();
        let dates: Vec<&str> = (0..rows_per_file)
            .map(|j| if j % 3 == 0 { "2024-01-01" } else if j % 3 == 1 { "2024-01-02" } else { "2024-01-03" })
            .collect();
        let values: Vec<i32> = (0..rows_per_file as i32).collect();

        let batch = RecordBatch::try_new(
            schema.clone(),
            vec![
                Arc::new(Int64Array::from(user_ids)),
                Arc::new(StringArray::from(dates)),
                Arc::new(Int32Array::from(values)),
            ],
        )
        .unwrap();

        let file = std::fs::File::create(&path).unwrap();
        let mut writer = ArrowWriter::try_new(file, schema.clone(), None).unwrap();
        writer.write(&batch).unwrap();
        writer.close().unwrap();

        paths.push(path.to_str().unwrap().to_string());
    }

    paths
}

criterion_group!(benches, bench_binpack, bench_sort);
criterion_main!(benches);
