/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright (C) Arm Ltd. 2022
 */

/*
 * Purpose:
 *   This program aims to stress CPU floating-point unit with multiplies.
 *
 * Theory:
 *   The program performs back-to-back double multiplications where the
 *   result of one operation is needed for the next operation.
 */

#include <stdlib.h>
#include "main.h"

static double kernel(long runs, double result, double mul) {
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
  double result;
  *((volatile double*)&result) = kernel(runs, 1e20, 2.1);
}
