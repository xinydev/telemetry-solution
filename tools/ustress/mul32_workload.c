/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright (C) Arm Ltd. 2022
 */

/*
 * Purpose:
 *   This program aims to stress CPU with back-to-back 32-bit integer multiplies.
 *
 * Theory:
 *   The program performs back-to-back 32-bit integer multiplications where the
 *   result of one operation is needed for the next operation.
 */

#include <stdint.h>
#include "main.h"

static int kernel(long runs, int32_t result, int32_t mul) {
  for(long n=runs; n>0; n--) {
    result *= mul;
    result *= mul;
    result *= mul;
    result *= mul;
  }
  return result;
}

static void stress(long runs) {
  /* This volatile use of result should prevent the computation from being optimised away by the compiler. */
  int32_t result;
  volatile int32_t a = 99, b = 457;
  *((volatile int32_t*)&result) = kernel(runs, a, b);
}
