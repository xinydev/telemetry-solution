/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright (C) Arm Ltd. 2022
 */

/*
 * Purpose:
 *   This program stresses the load-store pipeline with a memcpy entirely within L1D cache.
 *
 * Theory:
 *   The built-in mem copy is usally designed to be as efficient as possible, and may
 *   trigger the CPU prefetching hardware. This should fill up the load-store pipeline
 *   entirely with no dependency on other pipelines.
 */

#include <malloc.h>
#include <stdlib.h>
#include <string.h>
#include "cpuinfo.h"
#include "main.h"

static void stress(long runs) {
  char* mem = memalign(L1D_CACHE_SIZE, L1D_CACHE_SIZE);
  // Make sure the physical pages are allocated.
  // Reading from demand paging won't allocate physical memory. Instead, it
  // simply reads from a zerod page preallocated by kernel.
  memset(mem, 1, L1D_CACHE_SIZE);
  for(long n=runs; n>0; n--) {
    char* memA = mem;
    char* memB = mem + (L1D_CACHE_SIZE  / 2);
    memcpy(memB, memA, L1D_CACHE_SIZE / 2);
    // gcc 10.3 optimize the memcpy away without below line
    *(volatile char*)memB;
  }
  free(mem);
}
