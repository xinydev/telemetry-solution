/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright 2025 Arm Limited
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

void matmul (int n, double** A, double** B, double** C, int block_size) {
    // Multiply matrices: C = A * B
    for (int i = 0; i < n; i+=block_size)
        for (int k = 0; k < n; k+=block_size)
            for (int j = 0; j < n; j+=block_size)
                for (int ii = i; ii < i+block_size && ii < n; ii++)
                    for (int kk = k; kk < k+block_size && kk < n; kk++)
                        for (int jj = j; jj < j+block_size && jj < n; jj++)
                            C[ii][jj] += A[ii][kk] * B[kk][jj];
}

int main(int argc, char *argv[]) {
    if (argc < 2 || argc > 4) {
        fprintf(stderr, "Usage: %s <matrix_size> [--verify] [--bs=<block_size>]\n", argv[0]);
        return 1;
    }

    int block_size = 64;

    int verify = 0;
    for (int a = 2; a < argc; ++a) {
        if (strcmp(argv[a], "--verify") == 0) {
            verify = 1;
        } else if (strncmp(argv[a], "--bs=", 5) == 0) {
            block_size = atoi(argv[a] + 5);
            if (block_size <= 0) {
                fprintf(stderr, "Error: block size must be positive.\n");
                return 1;
            }
        } else {
            fprintf(stderr, "Unrecognized arg: %s\n", argv[a]);
            fprintf(stderr, "Usage: %s <matrix_size> [--verify] [--bs=<block_size>]\n", argv[0]);
            return 1;
        }
    }

    int n = atoi(argv[1]);
    if (n <= 0) {
        fprintf(stderr, "Error: matrix size must be a positive integer.\n");
        return 1;
    }

    // Allocate memory for the matrices
    double **A = malloc(n * sizeof(double *));
    double **B = malloc(n * sizeof(double *));
    double **C = malloc(n * sizeof(double *));
    for (int i = 0; i < n; i++) {
        A[i] = malloc(n * sizeof(double));
        B[i] = malloc(n * sizeof(double));
        C[i] = malloc(n * sizeof(double));
    }

    // Initialize matrices A and B
    for (int i = 0; i < n; i++)
        for (int j = 0; j < n; j++)
            A[i][j] = 1.0;

    for (int i = 0; i < n; i++)
        for (int j = 0; j < n; j++)
            B[i][j] = 1.0;

    // Initialize C to zero
    for (int i = 0; i < n; i++)
        for (int j = 0; j < n; j++)
            C[i][j] = 0.0;

    matmul(n, A, B, C, block_size);

    int failed = 0;
    if (verify) {
        for (int i = 0; i < n; i++) {
            for (int j = 0; j < n; j++) {
                if (C[i][j] != (double)n && !failed) {
                    printf("Mismatch at i=%d j=%d, result=%8.2f, expected=%8.2f\n", i, j, C[i][j],(double)n);
                    failed = 1;
                }
            }
        }

        if (failed) {
            printf("Test failed!\n");
        } else {
            printf("Test passed!\n");
        }
    }

    // Free memory
    for (int i = 0; i < n; i++) {
        free(A[i]);
        free(B[i]);
        free(C[i]);
    }
    free(A);
    free(B);
    free(C);

    return failed;
}

