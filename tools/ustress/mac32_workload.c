/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright (C) Arm Ltd. 2022
 */

/*
 * Purpose:
 *   This program aims to stress CPU with back-to-back 32-bit integer multiply-adds.
 *
 * Theory:
 *   The program performs back-to-back 32-bit integer multiply-adds where the
 *   result of one operation is needed for the next operation.
 */

#include <stdint.h>
#include "main.h"

#if USE_C

static int kernel(long runs, int32_t result, int32_t mul) {
  for(long n=runs; n>0; n--) {
    result += result * mul;
    result += result * mul;
    result += result * mul;
    result += result * mul;
  }
  return result;
}

#else

int kernel(long, int32_t, int32_t);

__asm__ (
"kernel:                \n"
"0:                     \n"
"madd    w1, w1, w2, w1 \n" // result += result * mul
"madd    w1, w1, w2, w1 \n" // result += result * mul
"madd    w1, w1, w2, w1 \n" // result += result * mul
"madd    w1, w1, w2, w1 \n" // result += result * mul
"subs    x0, x0, #1     \n" // n--
"bne     0b             \n"
"mov     w0, w1         \n"
"ret                    \n"
);

#endif

void stress(long runs) {
  /* This volatile use of result should prevent the computation from being optimised away by the compiler. */
  int32_t result;
  volatile int32_t a = 99, b = 457;
  *((volatile int32_t*)&result) = kernel(runs, a, b);
}
