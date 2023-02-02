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

static int64_t kernel(long runs, int64_t result, int64_t divider) {
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
  int64_t result;
  volatile int64_t a = INT64_MAX, b = 9;
  *((volatile int64_t*)&result) = kernel(runs, a, b);
}
