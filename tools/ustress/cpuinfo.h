/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright (C) Arm Ltd. 2022
 */

/*
 * Purpose:
 *   This file defines various characteristics of the CPU we are running on.
 */

#ifndef _CPU_INFO_H
#define _CPU_INFO_H

#define PAGE_SIZE (4096)

#if defined(CPU_NEOVERSE_V1)
  #define L1D_CACHE_ASSOCIATIVITY (4)
  #define L1D_CACHE_LINE_SIZE (64)
  #define L1D_CACHE_SIZE (64 * 1024)
  #define L1I_CACHE_SIZE (64 * 1024)
  #define L1D_TLB_SIZE (40)
  #define L2D_CACHE_ASSOCIATIVITY (8)
  #define L2D_CACHE_LINE_SIZE (64)
  #define L2D_CACHE_SIZE (1 * 1024 * 1024)
  #define STORE_BUFFER_SIZE (26) /* FIXME */
#elif defined(CPU_NEOVERSE_N1)
  #define L1D_CACHE_ASSOCIATIVITY (4)
  #define L1D_CACHE_LINE_SIZE (64)
  #define L1D_CACHE_SIZE (64 * 1024)
  #define L1I_CACHE_SIZE (64 * 1024)
  #define L1D_TLB_SIZE (48)
  #define L2D_CACHE_ASSOCIATIVITY (8)
  #define L2D_CACHE_LINE_SIZE (64)
  #define L2D_CACHE_SIZE (1 * 1024 * 1024)
  #define STORE_BUFFER_SIZE (26) /* FIXME */
#elif defined(CPU_NEOVERSE_N2)
  #define L1D_CACHE_ASSOCIATIVITY (4)
  #define L1D_CACHE_LINE_SIZE (64)
  #define L1D_CACHE_SIZE (64 * 1024)
  #define L1I_CACHE_SIZE (64 * 1024)
  #define L1D_TLB_SIZE (44)
  #define L2D_CACHE_ASSOCIATIVITY (8)
  #define L2D_CACHE_LINE_SIZE (64)
  #define L2D_CACHE_SIZE (1 * 1024 * 1024)
  #define STORE_BUFFER_SIZE (26) /* FIXME */
#elif defined(CPU_NONE)
  #error Please specify CPU. E.g. make CPU=NEOVERSE-V1.
#else
  #error Invalid CPU specified. Please check supported CPUs in cpuinfo.h.
#endif

#endif
