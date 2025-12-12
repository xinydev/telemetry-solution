# Matrix multiplication benchmarks

These benchmarks multiply dense `N × N` matrices using the formula `C = A × B`, where `A` and `B` are read-only inputs and `C` stores the result. They show how loop ordering and tiling change memory access patterns and performance.

In the descriptions below, `i`, `j`, and `k` refer to the conventional row, column, and inner-product loop indices used in textbook matrix multiplication:

- `matmul_baseline`: naïve `i-j-k` triple loop
- `matmul_ikj`: reorders the loops to `i-k-j` to increase contiguous accesses and improve locality
- `matmul_ikj_blocked`: adds a configurable tile size over `i`, `j`, and `k` to improve data reuse

Each binary accepts a matrix dimension `N` and optionally validates the result against a uniform input initialization. The block version also exposes `--bs=<block_size>` to tune the tile size. Default block size is 64.

## Building

From the matmul benchmark directory, use the provided `Makefile` to build all variants with an optimizing C compiler:

```sh
make
```

Override `CC`, `CFLAGS`, or `LDFLAGS` on the command line to adjust the toolchain. Invoke `make clean` to remove the generated executables.

## Running

Examples:

```sh
./matmul_baseline 512 --verify
./matmul_ikj 1024
./matmul_ikj_blocked 1024 --bs=128
```

The programs allocate `N^2` doubles for each matrix; ensure the system has sufficient memory for the requested dimension. The `--verify` option compares the computed output against the expected value and reports mismatches, which makes it useful when iterating on loop reordering, blocking, or other matmul optimizations.
