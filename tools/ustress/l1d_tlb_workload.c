/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright (C) Arm Ltd. 2022
 */

/*
 * Purpose:
 *   This program aims to stress the CPU DTLB0 with misses.
 *
 * Theory:
 *   The program iterates through memory with a stride equal to the
 *   page size. This results in repeated TLB accesses from different
 *   pages to overload the L1 DTLB entries.
 */

#include <assert.h>
#include <stdlib.h>
#include "cpuinfo.h"
#include "main.h"

static void stress(long runs) {
  // #pages = #TLB-entries * 4
  int maxSize = L1D_TLB_SIZE * 4;
  // guard buffer overflow
  int guardSize = maxSize + 10;
  assert((PAGE_SIZE + L1D_CACHE_LINE_SIZE) * (maxSize - 1) < (PAGE_SIZE * guardSize));

  volatile char* mem = aligned_alloc(PAGE_SIZE, PAGE_SIZE * guardSize);
  memset((void*)mem, 0, PAGE_SIZE * guardSize);
  char sum = 0;
  for(long n=runs; n>0; n--) {
    for(int set=0; set<maxSize; set++) {
      // offset by l1d cache line size to not triggering l1d cache miss
      sum += mem[(PAGE_SIZE * set) + (L1D_CACHE_LINE_SIZE * set)];
    }
  }
  mem[0] = sum;  // store sum to volatile address so that it is not optimized away
  free((void*)mem);
}
