/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Copyright (C) Arm Ltd. 2024
 */

#define _GNU_SOURCE

#include <linux/perf_event.h>
#include <sys/sysinfo.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define MAX_PMU 31

int main() {
    struct perf_event_attr pe = {
        .type           = PERF_TYPE_HARDWARE,
        .size           = sizeof(struct perf_event_attr),
        .config         = PERF_COUNT_HW_INSTRUCTIONS,
        .read_format    = PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING,
        .exclude_kernel = 1,
        .exclude_hv     = 1,
    };

    /*
     * The code will detect the number of PMUs for each CPU by scheduling itself
     * to run on subsequent CPUs. For each CPU, it will attempt to add perf
     * events to a group and then read the time when the event was enabled and
     * running. Comparing these two times can indicate if multiplexing was used.
     * If opening an event within the events group fails or if multiplexing is
     * detected, the number of PMUs for the given CPU is determined.
     */

    int max_cpu = get_nprocs_conf();
    cpu_set_t mask = {0};
    for (int cpu = 0; cpu < max_cpu; ++cpu) {
        CPU_SET(cpu, &mask);

        if (sched_setaffinity(0, sizeof(cpu_set_t), &mask) == 0) {
            int pmu_available = MAX_PMU;
            for (int pmu_count_attempt = 1; pmu_count_attempt <= pmu_available; ++pmu_count_attempt) {
                int fd[MAX_PMU];

                for (int pmu = 0; pmu < pmu_count_attempt; ++pmu)
                    fd[pmu] = syscall(__NR_perf_event_open, &pe, 0, cpu, pmu == 0 ? -1 : fd[0], 0);

                if (fd[pmu_count_attempt - 1] == -1) {
                    --pmu_count_attempt;
                    pmu_available = pmu_count_attempt;
                } else
                    for (int pmu = 0; pmu < pmu_count_attempt; ++pmu) {
                        struct {
                            uint64_t value;
                            uint64_t time_enabled;
                            uint64_t time_running;
                        } buf;
                        if (read(fd[pmu], &buf, sizeof(buf)) != sizeof(buf)) {
                            fputs("Error reading counter from perf event.\n", stderr);
                            exit(1);
                        }
                        if (buf.time_running < buf.time_enabled)
                            pmu_available = pmu_count_attempt - 1;
                    }

                for (int pmu = 0; pmu < pmu_count_attempt; ++pmu)
                    close(fd[pmu]);
            }
            printf("CPU #%d: %u PMUs\n", cpu, pmu_available);
        } else
            printf("CPU #%d: unknown number of PMUs\n", cpu);

        CPU_CLR(cpu, &mask);
    }

    return 0;
}
