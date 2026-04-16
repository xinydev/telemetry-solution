//Copyright (c) 2016-2024, ARM Limited. All rights reserved.
//
//SPDX-License-Identifier:        BSD-3-Clause
//
//Original code by: Ola Liljedahl, ola.liljedahl@arm.com

#ifndef _GNU_SOURCE
    #define _GNU_SOURCE
#endif

#ifdef __linux__
    #include <sched.h>
    #include <getopt.h>
    #include <arm_acle.h>

    #ifndef __isb
inline void __isb(void) {
    asm volatile("isb");
}

inline void __isb(int i) {
    (void)i;
    asm volatile("isb");
}
    #endif
#endif

#ifdef _WIN32
    #include <windows.h>
    #include "getopt.h"
#endif

#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <cmath>
#include <cinttypes>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <atomic>
#include <chrono>

#define MAXTHREADS 96
#define CACHE_LINE 64

extern "C" void work(uint32_t);

struct thread_information {
    unsigned int thr;
    int cpu;
};

static std::thread tid[MAXTHREADS];
static unsigned int NUMTHREADS = 8;
static unsigned int MAXNUMTHREADS = 0;
static uint64_t AFFINITY = ~0ULL;
static bool VERBOSE = false;
static unsigned int NUMITER = 40000000;
static unsigned int WORKITER = 200;
alignas(CACHE_LINE) static uint64_t LOCK_CTR; //Read and written in the critical section
static std::atomic<uint64_t> LOCK_SIMPLE{0};
alignas(CACHE_LINE) static std::atomic<uint64_t> THREAD_BARRIER;
static std::condition_variable all_done_cv;
static std::chrono::time_point<std::chrono::steady_clock> END_TIME;
alignas(CACHE_LINE) static unsigned int NUMLAPS[MAXTHREADS];
static unsigned int NUMCONS[MAXTHREADS];
static unsigned int NUMFAIL[MAXTHREADS];
static unsigned int RESULT_OPS[MAXTHREADS];
static float FAIRNESS[MAXTHREADS];


static inline void delay(unsigned int niter)
{
    for (unsigned int i = 0; i < niter; i++)
    {
        __isb(15);
    }
}


static void simple_lock_acquire(std::atomic<uint64_t> &lock, bool boffspin, bool boffcasf, unsigned int &numfailcas)
{
    for (;;)
    {
        uint64_t expected = 0;
        while (lock.load(std::memory_order_relaxed) != 0)
        {
            if (boffspin)
            {
                delay(10);
            }
        }
        //Lock free, try to grab it
        if (lock.compare_exchange_strong(expected, 1, std::memory_order_acquire))
        {
            //CAS success, lock acquired
            return;
        }
        //CAS failure, lock not taken
        ++numfailcas;
        if (boffcasf)
        {
            delay(2000);
        }
    }
}

static void
simple_lock_release(std::atomic<uint64_t> &lock)
{
    lock.store(0, std::memory_order_release);
}


static void lock_acquire(unsigned int &numfailcas)
{
    simple_lock_acquire(LOCK_SIMPLE, false, false, numfailcas);
}

static void lock_release()
{
    simple_lock_release(LOCK_SIMPLE);
}


//Wait for my signal to begin
static void barrier_thr_begin(unsigned int idx)
{
    uint64_t thrmask = 1ULL << idx;

    while ((THREAD_BARRIER.load(std::memory_order_acquire) & thrmask) == 0)
    {
        __isb(15);
    }
}

//Signal I am done
static void barrier_thr_done(unsigned int idx)
{
    uint64_t x = THREAD_BARRIER.fetch_and(~(1ULL << idx), std::memory_order_release);
    if ((x & (x - 1)) == 0)
    {
        //No threads left, we are the last thread to complete
        END_TIME = std::chrono::steady_clock::now();
        //Wake up the control thread
        all_done_cv.notify_one();
    }
}

//Signal all threads to begin
static void barrier_all_begin(unsigned int numthreads)
{
    uint64_t thrmask = numthreads < 64 ? (1ULL << numthreads) - 1 : ~0ULL;
    std::mutex all_done_mtx;
    std::unique_lock<std::mutex> lock(all_done_mtx);
    THREAD_BARRIER.store(thrmask, std::memory_order_release);
    //Block the control thread, free up a core
    all_done_cv.wait(lock);
}

//Wait until all threads are done
static void barrier_all_wait(void)
{
    while (THREAD_BARRIER.load(std::memory_order_acquire) != 0)
    {
        __isb(15);
    }
}

static void thr_execute(unsigned int tidx)
{
    bool done = false;
    uint64_t prev = -2;
    unsigned int numfailcas = 0, numcons = 0, numlaps = 0;
    while (!done)
    {
        lock_acquire(numfailcas);
        done = LOCK_CTR == NUMITER;
        if (!done)
        {
            if (prev + 1 == LOCK_CTR)
            {
                numcons++;
            }
            prev = LOCK_CTR++;
        }
        numlaps++;
        lock_release();
        work(WORKITER);
    }
    NUMLAPS[tidx] = numlaps;
    NUMCONS[tidx] = numcons;
    NUMFAIL[tidx] = numfailcas;
}

static void entrypoint(struct thread_information *arg)
{
    unsigned int tidx = arg->thr;
    int cpu = arg->cpu;

    delete arg;

#ifdef _WIN32
    HANDLE thread = GetCurrentThread();
    //Set affinity inside a thread
    if (cpu != -1)
    {
        GROUP_AFFINITY affinity = {1ULL << (cpu & 63), static_cast<uint16_t>(cpu >> 6)};
        SetThreadGroupAffinity(thread, &affinity, nullptr);
    }
    SetThreadPriority(thread, THREAD_PRIORITY_TIME_CRITICAL);
#else
    struct sched_param param = {1};
    //Set affinity inside a thread
    if (cpu != -1)
    {
        cpu_set_t cpuset = {0};
        CPU_SET(cpu, &cpuset);
        sched_setaffinity(0, sizeof(cpuset), &cpuset);
    }
    sched_setscheduler(0, SCHED_FIFO, &param);
#endif

    for (;;)
    {
        //Wait for my signal to start
        barrier_thr_begin(tidx);

        thr_execute(tidx);

        //Signal I am done
        barrier_thr_done(tidx);
    }
}

static void create_threads(unsigned int numthr, uint64_t affinity)
{
    struct thread_information *ti;
    for (unsigned int thr = 0; thr < numthr; thr++)
    {
        int cpu = -1;
        if (affinity != 0)
        {
#ifdef _WIN32
            _BitScanForward64(reinterpret_cast<unsigned long int*>(&cpu), affinity);
#else
            cpu = __builtin_ffsl(affinity) - 1;
#endif
            affinity &= ~(1ULL << cpu);
            if (VERBOSE)
                printf("Thread %u on CPU %u\n", thr, cpu);
        }

        ti = new thread_information;
        ti->thr = thr;
        ti->cpu = cpu;
        tid[thr] = std::thread(entrypoint, ti);
    }
}

static char* percent(char buf[], uint32_t x, uint32_t y)
{
    if (x != 0)
    {
        uint64_t z = 1000ULL * x / y;
        sprintf(buf, "%" PRIu64 ".%" PRIu64 "%%", z / 10, z % 10);
        return buf;
    }
    else
    {
        return const_cast<char*>("0");
    }
}

static void benchmark(unsigned int numthreads)
{
    LOCK_CTR = 0;
    memset(NUMLAPS, 0, sizeof(NUMLAPS));
    memset(NUMCONS, 0, sizeof(NUMCONS));
    memset(NUMFAIL, 0, sizeof(NUMFAIL));

    //Read starting time
    auto start = std::chrono::steady_clock::now();
    //Start worker threads
    barrier_all_begin(numthreads);
    //Wait for worker threads to complete
    barrier_all_wait();

    unsigned int numlaps = 0;
    for (unsigned int i = 0; i < numthreads; i++)
    {
        numlaps += NUMLAPS[i];
    }
    float fairness = 1.0;
    unsigned int fairops = numlaps / numthreads;
    for (unsigned int t = 0; t < numthreads; t++)
    {
        if (NUMLAPS[t] < fairops)
        {
            fairness *= static_cast<float>(NUMLAPS[t]) / static_cast<float>(fairops);
        }
        else if (NUMLAPS[t] > fairops)
        {
            fairness *= static_cast<float>(fairops) / static_cast<float>(NUMLAPS[t]);
        }
    }
    fairness = powf(fairness, 1.0 / numthreads);

    unsigned int numcons = 0;
    for (unsigned int i = 0; i < numthreads; i++)
    {
        numcons += NUMCONS[i];
    }
    unsigned int numfailcas = 0;
    for (unsigned int i = 0; i < numthreads; i++)
    {
        numfailcas += NUMFAIL[i];
    }

    auto elapsed_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(END_TIME - start).count();
    unsigned int elapsed_s = elapsed_ns / 1000000000ULL;
    unsigned int ops_per_sec = 0;
    if (elapsed_ns != 0)
    {
        ops_per_sec = static_cast<unsigned int>(1000000000ULL * numlaps / elapsed_ns);
        printf("%9" PRIu32 " ops/s", ops_per_sec);
    }
    else
    {
        printf("INF ops/s");
    }
    char bufc[40], buff[40];
    printf(", %u.%04llu secs, fairness %f, conseq %s, failcas %s, numthreads %u\n",
        elapsed_s,
        (elapsed_ns % 1000000000LLU) / 100000LLU,
        fairness,
        percent(bufc, numcons, numlaps),
        percent(buff, numfailcas, numlaps),
        numthreads
    );
    RESULT_OPS[numthreads - 1] = ops_per_sec;
    FAIRNESS[numthreads - 1] = fairness;
}

int main(int argc, char *argv[])
{
    int c;

    while ((c = getopt(argc, argv, "a:l:t:T:vw:")) != -1)
    {
        switch (c)
        {
            case 'a' :
                if (optarg[0] == '0' && optarg[1] == 'x')
                {
                    AFFINITY = strtoul(optarg + 2, nullptr, 16);
                }
                else
                {
                    AFFINITY = strtoul(optarg, nullptr, 2);
                }
                break;
            case 'l' :
                {
                    int numlaps = atoi(optarg);
                    if (numlaps < 1)
                    {
                        fprintf(stderr, "Invalid number of laps %d\n", numlaps);
                        exit(EXIT_FAILURE);
                    }
                    NUMITER = numlaps;
                    break;
                }
            case 't' :
                {
                    int numthreads = atoi(optarg);
                    if (numthreads < 1 || numthreads > MAXTHREADS)
                    {
                        fprintf(stderr, "Invalid number of threads %d\n", numthreads);
                        exit(EXIT_FAILURE);
                    }
                    NUMTHREADS = numthreads;
                    MAXNUMTHREADS = 0;
                    break;
                }
            case 'T' :
                {
                    int maxnumthreads = atoi(optarg);
                    if (maxnumthreads < 1 || maxnumthreads > MAXTHREADS)
                    {
                        fprintf(stderr, "Invalid number of maxnumthreads %d\n", maxnumthreads);
                        exit(EXIT_FAILURE);
                    }
                    MAXNUMTHREADS = maxnumthreads;
                    NUMTHREADS = 0;
                    break;
                }
            case 'v' :
                VERBOSE = true;
                break;
            case 'w' :
                {
                    int workiter = atoi(optarg);
                    if (workiter < 0)
                    {
                        fprintf(stderr, "Invalid number of work iterations %d\n", workiter);
                        exit(EXIT_FAILURE);
                    }
                    WORKITER = workiter;
                    break;
                }
            default :
                usage :
                    fprintf(stderr, "Usage: bm_lock <options>\n"
                        "-a <binmask>     CPU affinity mask (default base 2)\n"
                        "-l <numlaps>     Number of laps\n"
                        "-t <numthr>      Number of threads\n"
                        "-T <numthr>      Iterate over 1..T number of threads\n"
                        "-v               Verbose\n"
                        "-w <workiter>    Number of ADD/SUB work iterations\n"
                    );
                    exit(EXIT_FAILURE);
        }
    }
    if (optind != argc)
    {
        goto usage;
    }

    printf("%u laps, %u work iterations, %u thread%s, affinity mask=0x%" PRIx64 "\n",
        NUMITER,
        WORKITER,
        NUMTHREADS,
        NUMTHREADS != 1 ? "s" : "",
        AFFINITY
    );

#ifdef _WIN32
    SetPriorityClass(GetCurrentProcess(), REALTIME_PRIORITY_CLASS);
#endif

    if (MAXNUMTHREADS != 0)
    {
        create_threads(MAXNUMTHREADS, AFFINITY);
        for (unsigned int numthr = 1; numthr <= MAXNUMTHREADS; numthr++)
        {
            NUMTHREADS = numthr;
            benchmark(numthr);
        }
        for (unsigned int numthr = 1; numthr <= MAXNUMTHREADS; numthr++)
        {
            printf("%u%c",
                RESULT_OPS[numthr - 1],
                numthr < MAXNUMTHREADS ? ',' : '\n'
            );
        }
        for (unsigned int numthr = 1; numthr <= MAXNUMTHREADS; numthr++)
        {
            printf("%f%c",
                FAIRNESS[numthr - 1],
                numthr < MAXNUMTHREADS ? ',' : '\n'
            );
        }
    }
    else if (NUMTHREADS != 0)
    {
        create_threads(NUMTHREADS, AFFINITY);
        benchmark(NUMTHREADS);
    }

    _Exit(0);
}
