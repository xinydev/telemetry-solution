/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright (C) Arm Ltd. 2022
 */

/*
 * Purpose:
 *   This program aims to stress load after store scheduling.
 *
 * Theory:
 *   The program performs a large number of back-to-back loads to
 *   the same address as a preceeding store in order to stress
 *   load after store speculation. The address that is accessed is
 *   repeatedly changed so as to thwart attempts to spot load after
 *   store instances and handle them more efficiently.
 */

#include <stdint.h>
#include "cpuinfo.h"
#include "main.h"

static int64_t vars[64 * L1D_CACHE_LINE_SIZE];

static void stress(long runs) {
  for(int n=0; n<(64 * L1D_CACHE_LINE_SIZE);n++) {
    vars[n] = 1;
  }
  volatile int64_t* var1 = vars;
  volatile int8_t* var2 = (int8_t*)vars;
  #define W(i) var1[(i * L1D_CACHE_LINE_SIZE) / var1[0]]
  #define R(i) var2[(i * L1D_CACHE_LINE_SIZE * 8) + 0] + var2[(i * L1D_CACHE_LINE_SIZE * 8) + 1] + var2[(i * L1D_CACHE_LINE_SIZE * 8) + 2] + var2[(i * L1D_CACHE_LINE_SIZE * 8) + 3] + var2[(i * L1D_CACHE_LINE_SIZE * 8) + 4]
  for(long n=runs; n>0; n--) {
    W(0) = R(32);
    W(1) = R(0);
    W(2) = R(1);
    W(3) = R(2);
    W(4) = R(3);
    W(5) = R(4);
    W(6) = R(5);
    W(7) = R(6);
    W(8) = R(7);
    W(9) = R(8);
    W(10) = R(9);
    W(11) = R(10);
    W(12) = R(11);
    W(13) = R(12);
    W(14) = R(13);
    W(15) = R(14);
    W(16) = R(15);
    W(17) = R(16);
    W(18) = R(17);
    W(19) = R(18);
    W(20) = R(19);
    W(21) = R(20);
    W(22) = R(21);
    W(23) = R(22);
    W(24) = R(23);
    W(25) = R(24);
    W(26) = R(25);
    W(27) = R(26);
    W(28) = R(27);
    W(29) = R(28);
    W(30) = R(29);
    W(31) = R(30);
    W(32) = R(31);
  }
}
