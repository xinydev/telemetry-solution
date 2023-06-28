# UStress Validation Suite

UStress is a validation suite comprising a set of micro-architecture workloads that stress some of the major CPU resources like branch prediction units, execution units (arithmetic and memory), caches, and TLBs. These workloads can cause various performance bottleneck scenarios in the CPU.

## Workloads

The categories of workloads and their respective list of micro-benchmarks are as below.

- Branch: branch_direct_workload, branch_indirect_workload, call_return_workload
- Data Cache: l1d_cache_workload, l2d_cache_workload
- Instruction Cache: l1i_cache_workload
- Data TLB: l1d_tlb_workload
- Arithmetic Execution Units: div32_workload, …, fpdiv_workload, …, mul64_workload
- Memory Subsystem: memcpy_workload, store_buffer_full_workload, load_after_store_workload

## NOTES
- UStress is currently verified with **gcc-10.3** on **Neoverse-V1** and **N1**.
- The source code is sensitive to compiler optimization. Different compilers (and versions) may generate code with radically different behaviour.
- To support new micro-architecture, add related CPU configurations to *cpuinfo.h* and flag to *Makefile*.

## USAGE

* Use optional parameter `<multiplier>` to extend micro benchmark execution time.
  * For example `<benchmark_name> 2.5` command will extend execution time 2.5 times.
* Use `<benchmark_name> --help` for usage message.

## WOA BUILDS

**Makefile** supports LLVM `target=arm64-pc-windows-msvc` build. Users can generate micro benchmarks from source code.

Cross build: Users may want to open MSVC cross environment on their x64 machine with `vcvarsx86_arm64.bat`. **Makefile** supporting _WIN32 builds is tested in this environment.

### Limitations

For _WIN32 configuration two micro-benchmarks are explicitly disabled: `l1i_cache_workload` and `memcpy_workload`. This is due to compilation errors.