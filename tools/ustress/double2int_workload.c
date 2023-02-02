/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright (C) Arm Ltd. 2022
 */

/*
 * Purpose:
 *  This program performs repeated double to int conversions.
 */

#include "main.h"

static void stress(long runs) {
  double d = 2.345;
  int result = 0;
  for(long n=runs; n>0; n--) {
    result += (int)d;
    d += 0.1;
  }

  /* This volatile use of result should prevent the computation from being optimised away by the compiler. */
  *(volatile int*)&result = result;
}
