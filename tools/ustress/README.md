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
