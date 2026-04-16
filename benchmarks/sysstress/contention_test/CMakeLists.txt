# Copyright (c) 2025, Arm Limited. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

if(UNIX)
    enable_language(ASM)
    add_executable(bm_lock bm_lock.cc work.s)
    target_compile_options(bm_lock PRIVATE -O2)
    target_link_libraries(bm_lock PRIVATE pthread m)
elseif(WIN32)
    enable_language(ASM_MARMASM)
    add_executable(bm_lock bm_lock.cc work.asm getopt.c)
    target_link_options(bm_lock PRIVATE /STACK:8388608)
endif()
