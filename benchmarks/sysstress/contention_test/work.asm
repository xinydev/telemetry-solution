; Copyright (c) 2025, Arm Limited. All rights reserved.
; SPDX-License-Identifier: BSD-3-Clause
 EXPORT work
 AREA .text, CODE, READONLY, ARM64
work
    ISB     #15
    CBZ     W0, L1
; Align to 16 bytes, filled with NOP
; By default armasm64 fills with 0x0 (even in .text section)
; Padding word must be in range -2147483648 .. 2147483647
; NOP encoding is 0xD503201F = -721215457
 ALIGN 16, 0, -721215457
L0
    ADD     W0, W0, #1
    SUB     W0, W0, #2
    CBNZ    W0, L0
L1
    RET
 END
