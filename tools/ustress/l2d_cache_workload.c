/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright (C) Arm Ltd. 2022
 */

/*
 * Purpose:
 *   This program aims to stress L2D cache with misses.
 *
 * Theory:
 *   The program iterates through a block of memory whose size is twice
 *   that of the cache, by necessity forcing cache refills to occur. The
 *   accesses are actually targetted at the first cache index only, since
 *   exceeding the associativity at a single index is sufficient to
 *   generate refills. The access pattern is non-linear to twart trivial
 *   prefetching attempts.
 */

#include <stdlib.h>
#include "cpuinfo.h"
#include "main.h"

static void stress(long runs) {
  /* Create and initialise a block of memory with a non-linear pointer chain. */
  size_t memSize = L2D_CACHE_SIZE * 8;
  void** mem = malloc(memSize);
  int stepSize = (L2D_CACHE_LINE_SIZE * L2D_CACHE_ASSOCIATIVITY) / sizeof(void*);
  int maxSet = memSize / (stepSize * sizeof(void*));
  int idx = 0;
  for(int set = 1; set < maxSet; set++) {
    int idxNext = (set & 1) ? (maxSet - set) : set;
    mem[stepSize * idx] = &mem[stepSize * idxNext];
    idx = idxNext;
  }
  mem[stepSize * idx] = NULL;

  /* Repeatedly follow the pointer chain to generate cache refills. */
  int sum = 0;
  for(long n=runs; n>0; n--) {
    void** next = mem;
    do {
      sum++;
      next = (void**)*next;
    } while(next != NULL);
  }

  /* This volatile use of result should prevent the computation from being optimised away by the compiler. */
  *((volatile int*)&sum) = sum;

  free((void*)mem);
}
