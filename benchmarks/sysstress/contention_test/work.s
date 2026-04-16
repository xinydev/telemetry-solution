// Copyright (c) 2025, Arm Limited. All rights reserved.
// SPDX-License-Identifier: BSD-3-Clause
.arch armv8-a
.globl work
.section .text,"ax",%progbits
work:
ISB     #15
CBZ     W0, 1f
.align 4
0:
ADD     W0, W0, #1
SUB     W0, W0, #2
CBNZ    W0, 0b
1:
RET
