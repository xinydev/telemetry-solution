/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright 2025 Arm Limited
 */

#include <climits>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>

namespace {
constexpr int payload_size = 64;
static_assert(payload_size % sizeof(int) == 0,
              "Payload size must be a multiple of sizeof(int).");
constexpr int payload_ints = payload_size / sizeof(int);

// Executes the pointer-chasing loop, optionally prefetching upcoming targets before
// copying each payload into the output buffer.
void bulk_search(
    int lookups,
    const int* input_lookups,
    int* output_data,
    int* const* ptr_list,
    int prefetch_distance
) {
    for (int i = 0; i < lookups; ++i) {
        if (prefetch_distance > 0) {
            const int next_index = i + prefetch_distance;
            if (next_index < lookups) {
                __builtin_prefetch(ptr_list[input_lookups[next_index]]);
            }
        }
        const int* next = ptr_list[input_lookups[i]];
        std::memcpy(output_data + (i * payload_ints), next, payload_size);
    }
}

void print_usage(const char* prog_name) {
    std::fprintf(stderr,
                 "Usage: %s <array_size> <num_lookups> [--prefetch-distance N] [--verify]\n",
                 prog_name);
}

}  // namespace

int main(int argc, char* argv[]) {
    if (argc < 3) {
        print_usage(argv[0]);
        return 1;
    }

    int size = std::atoi(argv[1]);
    if (size < 1) {
        std::fprintf(stderr, "Array size must be a positive integer.\n");
        return 1;
    }

    int lookups = std::atoi(argv[2]);
    if (lookups < 1) {
        std::fprintf(stderr, "Number of lookups must be a positive integer.\n");
        return 1;
    }

    int prefetch_distance = 0;
    bool verify = false;

    for (int i = 3; i < argc; ++i) {
        if (std::strcmp(argv[i], "--verify") == 0) {
            verify = true;
        } else if (std::strcmp(argv[i], "--prefetch-distance") == 0) {
            if (i + 1 >= argc) {
                std::fprintf(stderr, "--prefetch-distance requires an integer argument.\n");
                print_usage(argv[0]);
                return 1;
            }
            prefetch_distance = std::atoi(argv[++i]);
            if (prefetch_distance < 0) {
                std::fprintf(stderr, "Prefetch distance must be zero or a positive integer.\n");
                return 1;
            }
        } else {
            std::fprintf(stderr, "Unrecognized argument: %s\n", argv[i]);
            print_usage(argv[0]);
            return 1;
        }
    }

    int** ptr_list = new int*[size];
    int* input_lookups = new int[lookups];
    int* output_data = new int[static_cast<std::size_t>(lookups) * payload_ints];

    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<> lookup_distr(0, size - 1);

    for (int i = 0; i < size; ++i) {
        ptr_list[i] = new int[payload_ints];
        for (int j = 0; j < payload_ints; ++j) {
            ptr_list[i][j] = i;
        }
    }

    for (int i = 0; i < lookups; ++i) {
        input_lookups[i] = lookup_distr(gen);
    }

    bulk_search(lookups, input_lookups, output_data, ptr_list, prefetch_distance);

    int exit_code = 0;
    if (verify) {
        bool mismatch_found = false;
        for (int i = 0; i < lookups && !mismatch_found; ++i) {
            for (int j = 0; j < payload_ints; ++j) {
                if (output_data[i * payload_ints + j] != input_lookups[i]) {
                    std::printf("Data payload %d offset %d doesn't match for idx %d\n",
                                output_data[i * payload_ints + j], j, input_lookups[i]);
                    exit_code = 1;
                    mismatch_found = true;
                    break;
                }
            }
        }
        if (!mismatch_found) {
            std::printf("Passed!\n");
        }
    }

    for (int i = 0; i < size; ++i) {
        delete[] ptr_list[i];
    }
    delete[] ptr_list;
    delete[] input_lookups;
    delete[] output_data;

    return exit_code;
}
