# All-algorithm benchmark — 2026-09-18

## Configuration and methodology

- Same Release binary: AppleClang 14.0.3.14030022, `-O3 -DNDEBUG -flto=thin`, macOS arm64, 8 logical CPUs.
- Graph with 38,148 vertices / 42,170 directed edges; 1,000 fixed evaluation queries, seed 162163. No configuration retuning.
- HPA/BiHPA: `L=3500 m`, `w=1.05`, stored shortcut paths, per-query heuristic caching, reusable workspaces; workers share the index.
- 1 and 4 workers, queue capacity 64. One worker calls Router directly; four workers use QueryPool.
- Each configuration: 4 independent processes, 2 warm-up batches and 10 measured batches/process. In total: 32 sequential processes, 320 measured batches, 320,000 query calls.
- Algorithm order rotates each round; 1/4-worker order alternates. Benchmarks do not run concurrently and compete for CPU.
- Table values are medians across processes. p95 is calculated per process, then the median is taken. All outliers are retained; individual processes and ranges are fully recorded.

Service is the time to process one query, including path construction. Batch wall time includes submitting and collecting the whole batch; throughput is query count divided by batch wall time. Graph loading, index construction/setup, file writes, and checking are outside query/batch timing. Diagnostics are disabled during latency measurements. Each metric is independently median-aggregated, so the reciprocal of median batch wall time need not equal median throughput.

## Latency and throughput

| Algorithm | Worker | Mean service (µs) | p95 service (µs) | Batch of 1,000 queries (ms) | Throughput (query/s) |
|---|---:|---:|---:|---:|---:|
| Dijkstra | 1 | 1257.966 | 2533.542 | 1258.094 | 831.3 |
| A* | 1 | 648.079 | 2402.604 | 648.117 | 1553.4 |
| HPA | 1 | 170.712 | 497.020 | 170.745 | 6066.3 |
| BiHPA | 1 | 197.479 | 626.645 | 197.523 | 5561.3 |
| Dijkstra | 4 | 1850.031 | 3886.625 | 464.350 | 2486.8 |
| A* | 4 | 1043.409 | 3874.417 | 262.298 | 4377.2 |
| HPA | 4 | 226.053 | 652.792 | 57.123 | 18850.2 |
| BiHPA | 4 | 230.425 | 729.770 | 58.301 | 18391.3 |

| Algorithm | Throughput: 4 workers / 1 worker |
|---|---:|
| Dijkstra | 2.991× |
| A* | 2.818× |
| HPA | 3.107× |
| BiHPA | 3.307× |

This speeds up concurrent processing of multiple queries; each query is still executed by one worker. Mean service time can increase with four workers even while the entire batch finishes sooner.

## Variation between processes

Some runs were noticeably slower, particularly from the third round onward. These measurements cannot isolate background load, scheduling, or CPU frequency as the cause. Cores were not pinned and frequency was not locked. Read ranges alongside medians; small HPA/BiHPA differences should not be treated as stable conclusions.

| Algorithm | Worker | Mean service min–max (µs) | Throughput min–max (query/s) |
|---|---:|---:|---:|
| Dijkstra | 1 | 992.954–1778.074 | 562.4–1007.0 |
| Dijkstra | 4 | 1115.338–2638.109 | 1510.4–3572.9 |
| A* | 1 | 573.729–703.467 | 1421.4–1742.9 |
| A* | 4 | 666.931–1440.070 | 2762.9–5963.9 |
| HPA | 1 | 138.334–217.391 | 4599.1–7227.6 |
| HPA | 4 | 157.036–286.808 | 13808.6–25166.3 |
| BiHPA | 1 | 137.848–359.203 | 2783.4–7252.7 |
| BiHPA | 4 | 164.990–301.965 | 13107.5–23933.5 |

## Correctness and quality

All 320 measured batches passed the checker. Dijkstra/A* use `optimal`; HPA/BiHPA use `any` with a w-bound check. For each algorithm, returned paths are byte-identical across repetitions, processes, and worker counts. Quality is calculated over 1,000 distinct queries; repeated measurements do not multiply the sample count.

| Algorithm | Valid paths | Matching optimum | p95 gap | Max gap |
|---|---:|---:|---:|---:|
| Dijkstra | 1000/1000 | 1000/1000 | 0.000000% | 0.000000% |
| A* | 1000/1000 | 1000/1000 | 0.000000% | 0.000000% |
| HPA | 1000/1000 | 901/1000 | 0.014112% | 1.251586% |
| BiHPA | 1000/1000 | 886/1000 | 0.065208% | 1.622314% |

Dijkstra/A* gaps on the order of 10⁻¹³% are floating-point error and are rounded to 0 in the table. HPA/BiHPA use Weighted search; their speed comes with the path quality shown above.

## Setup and memory

| Algorithm | Worker | Load graph (ms) | Setup (ms) | Index build (ms) | Index (bytes) | Workspace HPA/worker (bytes) | Peak RSS (MiB) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Dijkstra | 1 | 158.169 | 0.000 | — | — | — | 7.281 |
| Dijkstra | 4 | 153.719 | 0.048 | — | — | — | 8.648 |
| A* | 1 | 120.895 | 0.000 | — | — | — | 7.328 |
| A* | 4 | 163.336 | 0.044 | — | — | — | 9.094 |
| HPA | 1 | 143.555 | 18.023 | 18.006 | 3187800 | 1189236 | 15.031 |
| HPA | 4 | 137.410 | 17.901 | 17.770 | 3187800 | 1189236 | 16.430 |
| BiHPA | 1 | 146.212 | 18.182 | 18.165 | 4697784 | 2480888 | 15.273 |
| BiHPA | 4 | 143.223 | 17.822 | 17.755 | 4697784 | 2480888 | 23.172 |

The index is shared and does not multiply with worker count. Workspace is the largest capacity observed per worker; the runner measures workspace only for HPA/BiHPA. Peak RSS covers the entire process, including graph, queues, workspaces, and retained paths. Graph loading is affected by the page cache; the OS cache is not cleared.

## Data and reproduction

The [summary JSON](../benchmarks/algorithms-20260918.json) contains individual processes, hashes, the run schedule, short/medium/long group statistics, and quality.
The [raw report](../artifacts/algorithms-20260918-162732/report.json) and [summary CSV](../artifacts/algorithms-20260918-162732/summary.csv) accompany timing CSVs, per-batch results/checker output, and logs. The session includes a binary snapshot, flags, and `run.py`; artifacts are ignored by Git.

Binary SHA-256: `cf873f7f92d22f2de16db96bd58e729f0bbf6b672d17386b856dafc9fc9b6a6b`.

Example running one process per algorithm with the same batch protocol:

```sh
.venv/bin/python scripts/benchmark.py \
  --algorithm dijkstra astar hpa bihpa \
  --threads 4 --queue-capacity 64 --warmup 2 --repetitions 10
```

Replace `--threads 4` with `1` for sequential measurement. Do not pass `--mode` when measuring all four algorithms together, so the runner chooses the appropriate checker. Current HPA defaults are L=3500, w=1.05. The full 32-process schedule is in the session manifest and runner.
