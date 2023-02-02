/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright (C) Arm Ltd. 2022
 */

/*
 * Purpose:
 *   This program aims to stress the CPU store buffer.
 *
 * Theory:
 *   The program performs a large number of back-to-back writes to
 *   memory in order to saturate the CPU store buffer. The addresses
 *   written to by each store are in different cache lines to prevent
 *   them being merged in the store buffer. The code marks the writes
 *   as volatile to prevent the compiler from optimizing them away.
 */

#include <stdlib.h>
#include "cpuinfo.h"
#include "main.h"

static void stress(long runs) {
  volatile char* mem = malloc(L1D_CACHE_LINE_SIZE * STORE_BUFFER_SIZE * 2);
  for(long n=runs; n>0; n--) {
    for(int s=0; s<STORE_BUFFER_SIZE * 2; s++) {
      mem[L1D_CACHE_LINE_SIZE * s] = (char)n;
    }
  }
  free((void*)mem);
}
