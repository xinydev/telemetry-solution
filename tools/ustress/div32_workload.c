/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright (C) Arm Ltd. 2022
 */

/*
 * Purpose:
 *   This program aims to stress CPU with back-to-back 32-bit integer divides.
 *
 * Theory:
 *   The program performs back-to-back 32-bit integer divisions where the
 *   result of one operation is needed for the next operation.
 */

#include <limits.h>
#include <stdint.h>
#include "main.h"

static int kernel(long runs, int32_t result, int32_t divider) {
  for(long n=runs; n>0; n--) {
    result /= divider;
    result /= divider;
    result /= divider;
    result /= divider;
  }
  return result;
}

static void stress(long runs) {
  /* This volatile use of result should prevent the computation from being optimised away by the compiler. */
  int32_t result;
  volatile int32_t a = INT32_MAX, b = 9;
  *((volatile int32_t*)&result) = kernel(runs, a, b);
}
