# Random pointer access benchmark

This microbenchmark performs random lookups into an array of 64-byte payloads through a pointer table. It is useful for evaluating irregular memory accesses and the effect of software prefetching.

## Building

From the random pointer access benchmark directory, use the provided `Makefile` to build the binary with an optimizing C++ compiler:

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

On 64-bit systems, the program allocates `72` bytes for each array entry and `68` bytes for each lookup entry; ensure the system has sufficient memory for the requested `array_size` and `num_lookups`.
