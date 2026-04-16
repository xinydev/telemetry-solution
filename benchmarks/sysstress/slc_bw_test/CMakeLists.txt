# Copyright (c) 2025, Arm Limited. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

add_executable(slc_bw ../stream/stream.c)

# Add defines
target_compile_definitions(slc_bw PRIVATE NTIMES=200000)
target_compile_definitions(slc_bw PRIVATE TESTREPS=4)
target_compile_definitions(slc_bw PRIVATE STREAM_ARRAY_SIZE=5000000)

if(UNIX)
    find_package(OpenMP REQUIRED)
    target_compile_options(slc_bw PRIVATE -O2 -fopenmp)
    target_link_libraries(slc_bw PRIVATE pthread m OpenMP::OpenMP_C)
elseif(WIN32)
    target_compile_options(slc_bw PRIVATE /openmp)
    # This program on Windows requires at least 8 MB of stack size
    target_link_options(slc_bw PRIVATE /STACK:8388608)
endif()
