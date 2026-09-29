from cryo.scanner.analyze import FILE_SIZE_BIN_COUNT, record_file_size_bin


def test_file_size_bins_are_log2_and_sum_to_the_file_count() -> None:
    bins = [0] * FILE_SIZE_BIN_COUNT
    record_file_size_bin(bins, 0)
    record_file_size_bin(bins, 40)
    record_file_size_bin(bins, 2**20)
    record_file_size_bin(bins, 2**40)
    assert sum(bins) == 4
    assert bins[0] == 1
    assert bins[5] == 1
    assert bins[20] == 1
    assert bins[-1] == 1
