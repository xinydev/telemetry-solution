/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright (C) Arm Ltd. 2022
 */

/*
 * Purpose:
 *   This program aims to stress CPU call return prediction.
 *
 * Theory:
 *   The program creates a long chain of calls to randomly selected functions.
 *   Each function uses the return value from the function it calls in a
 *   further computation so that the calls are not optimised away by the
 *   compiler. The call stack thus ends up with a long sequence of return
 *   addresses that are to the CPU non-predictable.
 */

#include <stdint.h>
#include <stdlib.h>
#include "main.h"

static uint16_t fA(int n, uint16_t p);
static uint16_t fB(int n, uint16_t p);
static uint16_t fC(int n, uint16_t p);
static uint16_t fD(int n, uint16_t p);
static uint16_t fE(int n, uint16_t p);
static uint16_t fF(int n, uint16_t p);
static uint16_t fG(int n, uint16_t p);
static uint16_t fH(int n, uint16_t p);

static uint16_t (*calls[])(int n, uint16_t p) = {
  &fA, &fB, &fC, &fD, &fE, &fF, &fG, &fH
};

static uint16_t fA(int n, uint16_t lfsr) {
  n--;
  if (n == 0) return lfsr;
  lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
  return calls[lfsr & 7](n, lfsr) ^ 128;
}

static uint16_t fB(int n, uint16_t lfsr) {
  n--;
  if (n == 0) return lfsr;
  lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
  return calls[lfsr & 7](n, lfsr) ^ 64;
}

static uint16_t fC(int n, uint16_t lfsr) {
  n--;
  if (n == 0) return lfsr;
  lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
  return calls[lfsr & 7](n, lfsr) ^ 32;
}

static uint16_t fD(int n, uint16_t lfsr) {
  n--;
  if (n == 0) return lfsr;
  lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
  return calls[lfsr & 7](n, lfsr) ^ 16;
}

static uint16_t fE(int n, uint16_t lfsr) {
  n--;
  if (n == 0) return lfsr;
  lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
  return calls[lfsr & 7](n, lfsr) ^ 8;
}

static uint16_t fF(int n, uint16_t lfsr) {
  n--;
  if (n == 0) return lfsr;
  lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
  return calls[lfsr & 7](n, lfsr) ^ 4;
}

static uint16_t fG(int n, uint16_t lfsr) {
  n--;
  if (n == 0) return lfsr;
  lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
  return calls[lfsr & 7](n, lfsr) ^ 2;
}

static uint16_t fH(int n, uint16_t lfsr) {
  n--;
  if (n == 0) return lfsr;
  lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 2) ^ (lfsr >> 3) ^ (lfsr >> 5) ) & 1) << 15);
  return calls[lfsr & 7](n, lfsr) ^ 1;
}

static void stress(long runs) {
  uint16_t r = 0xACE1u;
  for(int n = runs; n>0; n--) {
    r = fA(10000, r);
  }

  /* This volatile use of result should prevent the above code from being optimised away by the compiler. */
  *((volatile uint16_t*)&r) = r + 1;
}
