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
To modify runtime, change the define for NTIMES in the CMakeLists.txt
Execute using ./mem_bw

### SLC Bandwidth Test

To modify data size, change the define for STREAM_ARRAY_SIZE such that three double arrays fit within in the sum of total L2 cache and shared cache. Example: `STREAM_ARRAY_SIZE = (Total_L2_B + (2*Total_SLC_B/3) ) / 24)`.

This is a streaming ADD test, calculating C=A+B on arrays sized to fit in system cache.
To modify runtime, change the define for TESTREPS in the CMakeLists.txt
Execute using ./slc_bw

### Contention Test

This is a test where all threads attempt to acquire a simple spinlock.
A counter is modified in between acquire and release of the spinlock.
A small delay is added after release to avoid the releasing thread re-acquiring the lock.
Because of this delay, there may be some slack at very low thread counts, so results for fewer than 4 threads may not be intuitive.
Execute using ./contention -t <thread count>
To modify runtime, change the define for NUMITER or pass "-l <iterations>" as an argument
