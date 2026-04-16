# Sysstress

Sysstress is a set of micro tests to stress memory system performance


# Installation

Create a build directory and build the executables:
```sh
mkdir build
cd build
cmake ..
make
```
After building, you will find the executables in:
1. build/contention_test/bm_lock
1. build/mem_bw_test/mem_bw
1. build/slc_bw_test/slc_bw

# Tests Included

### Memory Bandwidth Test

This is a streaming ADD test, calculating C=A+B on large arrays.
To modify runtime, change the define for NTIMES
Execute using ./mem_bw

