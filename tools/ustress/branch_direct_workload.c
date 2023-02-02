/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright (C) Arm Ltd. 2022
 */

/*
 * Purpose:
 *   This program aims to stress CPU direct branch predicition.
 *
 * Theory:
 *   The program consists of a sequence of direct branches (the if statements). The
 *   direction of each branch is determined by a simple pseudo random number generator
 *   that should be sufficient to defeat most branch predictors. The random number is
 *   computed immediately prior to each branch and each ramdon number depends on the
 *   previous one; these techniques should reduce the possibility for the processor
 *   to compute the branch direction well in advance of each branch.
 *
 *   It is advisable to check the disassembly to ensure that the compiler has indeed
 *   generated a conditional direct branch for each if statement and has not optimised
 *   these in some other way.
 */

#include <stdint.h>
#include <stdlib.h>
#include "main.h"

static void stress(long runs) {
  uint16_t lfsr = 0xACE1u;
  int result = 0;
  for(long n=runs; n>0; n--) {
    lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
    if (lfsr & 1) result = (result << 1) ^ 11;

    lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
    if (lfsr & 1) result = (result << 1) ^ 11;

    lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
    if (lfsr & 1) result = (result << 1) ^ 11;

    lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
    if (lfsr & 1) result = (result << 1) ^ 11;

    lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
    if (lfsr & 1) result = (result << 1) ^ 11;

    lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
    if (lfsr & 1) result = (result << 1) ^ 11;

    lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
    if (lfsr & 1) result = (result << 1) ^ 11;

    lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
    if (lfsr & 1) result = (result << 1) ^ 11;

    lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
    if (lfsr & 1) result = (result << 1) ^ 11;

    lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
    if (lfsr & 1) result = (result << 1) ^ 11;

    lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
    if (lfsr & 1) result = (result << 1) ^ 11;

    lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
    if (lfsr & 1) result = (result << 1) ^ 11;

    lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
    if (lfsr & 1) result = (result << 1) ^ 11;

    lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
    if (lfsr & 1) result = (result << 1) ^ 11;

    lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
    if (lfsr & 1) result = (result << 1) ^ 11;

    lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
    if (lfsr & 1) result = (result << 1) ^ 11;
  }

  /* This volatile use of result should prevent the above code from being optimised away by the compiler. */
  *((volatile int*)&result) = result + 1;
}
