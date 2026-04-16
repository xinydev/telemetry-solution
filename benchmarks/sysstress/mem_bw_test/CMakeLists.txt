# Copyright (c) 2025, Arm Limited. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

add_executable(mem_bw ../stream/stream.c)

# Add defines
target_compile_definitions(mem_bw PRIVATE NTIMES=20000)
target_compile_definitions(mem_bw PRIVATE TESTREPS=1)
#------------------------------------------------------------------------------
# The Windows executable loader imposes a 2 GB limit on the total size of all
# PE sections, including the .bss section, which is not stored on disk.
# Therefore, the arrays declared below must remain under 2 GB, even though it
# resides in .bss and doesn't consume disk space.
#----------------------------------------------------------------------------*/
if(WIN32)
target_compile_definitions(mem_bw PRIVATE STREAM_ARRAY_SIZE=80000000)
else()
target_compile_definitions(mem_bw PRIVATE STREAM_ARRAY_SIZE=100000000)
endif()

if(UNIX)
    find_package(OpenMP REQUIRED)
    target_compile_options(mem_bw PRIVATE -O2 -fopenmp)
    target_link_libraries(mem_bw PRIVATE pthread m OpenMP::OpenMP_C)
elseif(WIN32)
    target_compile_options(mem_bw PRIVATE /openmp)
    # This program on Windows requires at least 8 MB of stack size
    target_link_options(mem_bw PRIVATE /STACK:8388608)
endif()
