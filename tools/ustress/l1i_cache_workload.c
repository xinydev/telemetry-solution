/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright (C) Arm Ltd. 2022
 */

/*
 * Purpose:
 *   This program aims to stress CPU L1I cache with misses.
 *
 * Theory:
 *   The program makes repeated calls to functions that are aligned to
 *   page boundaries. Given sufficient different function calls then
 *   the calls exceed the associativity of the L1I cache and cause
 *   misses.
 */

#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include "cpuinfo.h"
#include "main.h"

#define FUNC(f) \
  static __attribute__((aligned(L1I_CACHE_SIZE))) void f(void* fp) { \
    void (**funcPtr)(void*) = fp; \
    void(*func)(void*) = *(funcPtr++); \
    (*func)(funcPtr); \
  }

FUNC(fA)
FUNC(fB)
FUNC(fC)
FUNC(fD)
FUNC(fE)
FUNC(fF)
FUNC(fG)
FUNC(fH)
FUNC(fI)
FUNC(fJ)
FUNC(fK)
FUNC(fL)

static __attribute__((aligned(L1I_CACHE_SIZE))) void fZ(void* fp) { \
}

static void (*funcs[])(void*) = {
  &fA, &fB, &fC, &fD, &fE, &fF, &fG, &fH, &fI, &fJ, &fK, &fL,
  &fZ
};

static void assertFuncsArePageAligned(void) {
  void(**funcPtr)(void*) = funcs;
  uintptr_t pageMask = (uintptr_t)(L1I_CACHE_SIZE - 1);
  while(*funcPtr) {
    uintptr_t diff = (uintptr_t)(*funcPtr) & pageMask;
    assert(diff == 0);
    funcPtr++;
  }
}

static void assertFuncsAreDistinct(void) {
  void(**funcPtr1)(void*) = funcs;
  while(*funcPtr1) {
    void(**funcPtr2)(void*) = funcPtr1 + 1;
    while(*funcPtr2) {
      assert(*funcPtr1 != *funcPtr2);
      funcPtr2++;
    }
    funcPtr1++;
  }
}

static void stress(long runs) {
  assertFuncsArePageAligned();
  assertFuncsAreDistinct();
  for(volatile long n=runs; n>0; n--) {
    (*funcs[0])((void*)funcs);
  }
}
