# Random Pointer Access Benchmark

This microbenchmark models pointer-chasing workloads that issue random memory accesses into a large array of cache-line-sized payloads. It is useful for evaluating memory latency, software prefetch effectiveness, and the impact of irregular access patterns on Arm Neoverse platforms.

## Building

Use the provided `Makefile` to build the binary with an optimizing C++ compiler:

```sh
make
```

Set `CXX`, `CXXFLAGS`, or `LDFLAGS` on the command line to override the defaults. Run `make clean` to remove the compiled binary.

## Running

The executable accepts the number of payload slots (`array_size`), the number of random lookups to execute, and optional flags:

```sh
./random_pointer_access <array_size> <num_lookups> [--prefetch-distance N] [--verify]
```

- If `--prefetch-distance` is omitted or zero, the benchmark performs lookups without software prefetching.
- Setting a positive `N` issues a lookahead preload before each dereference so you can explore the effect of prefetch distance tuning.
- Passing `--verify` performs a correctness check on the gathered payloads.

Example:

```sh
./random_pointer_access 1048576 2000000 --prefetch-distance 8
./random_pointer_access 1048576 2000000 --verify
```

## Notes

- The program allocates its working set on the heap; ensure the system has enough free memory for the requested capacity.