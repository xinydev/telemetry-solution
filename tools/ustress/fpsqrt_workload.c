/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright (C) Arm Ltd. 2022
 */

/*
 * Purpose:
 *   This program aims to stress CPU floating-point unit with square roots.
 *
 * Theory:
 *   The program peforms back-to-back double square roots where the
 *   result of one operation is needed for the next operation.
 */

#include <math.h>
#include <stdlib.h>
#include "main.h"

static double kernel(long runs, double result) {
  for(long n=runs; n>0; n--) {
    result = sqrt(result);
    result = sqrt(result);
    result = sqrt(result);
    result = sqrt(result);
  }
  return result;
}

static void stress(long runs) {
  /* This volatile use of result should prevent the computation from being optimised away by the compiler. */
  double result;
  *((volatile double*)&result) = kernel(runs, 1e20);
}
