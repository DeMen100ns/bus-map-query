# HPA and bidirectional HPA: results for 2026-09-18

BiHPA works correctly, but has **no overall speed advantage yet** on the current workload. It reduces vertex expansions by about 14.5% and is faster on short/medium queries, but slower on long queries. The one-worker mean is nearly identical to HPA; p95 and memory usage are higher. Both commands are retained for comparison; BiHPA does not replace current HPA.

## Measurement

HCMC graph: 38,148 vertices, 42,170 directed edges. The existing 1,000-query suite and Python oracle are unchanged; short/medium/long groups contain 334/333/333 queries. Fixed `L = 3500 m`, `w = 1.05`, with the same full shortcut paths; BiHPA is not tuned on the evaluation suite.

Same Release binary, AppleClang, macOS 26.5 arm64. Each algorithm/worker-count pair runs 6 independent processes, each with 2 warm-up and 10 measured batches. Balanced AB/BA order is shuffled with seed 162164; benchmarks run sequentially. One worker calls Router directly; four workers use QueryPool with queue capacity 64. Each index is built once before queries and shared by workers. Results are not cached.

Tables show medians across processes. Mean/p95 service within each process is calculated from 10,000 query calls, including complete path concatenation. Checking, file writes, graph loading, and setup are outside the query timer. Quality uses 1,000 distinct queries without multiplying by repetitions. Processes have no core pinning or frequency locking; background load and thermal conditions affect measurements, especially with four workers.

## Performance

| Algorithm | Worker | Mean service (µs/query) | p95 service (µs) | Throughput (query/s) |
|---|---:|---:|---:|---:|
| HPA | 1 | 145.020 | 417.667 | 6894.8 |
| BiHPA | 1 | 144.938 | 456.584 | 6898.3 |
| HPA | 4 | 212.567 | 589.730 | 18615.9 |
| BiHPA | 4 | 216.017 | 682.916 | 18309.8 |

The one-worker mean differs by only 0.057%, too little to establish improvement; BiHPA p95 increases 9.3%. Process means range from 143.494–154.622 µs for HPA and 143.045–148.234 µs for BiHPA. BiHPA four-worker throughput is 1.64% lower, but process variation is large: mean service is 179.079–256.972 µs for HPA and 175.504–296.820 µs for BiHPA. This small difference is not statistical evidence that either version is always faster or slower.

Four-worker service measures one query while workers compete for resources; it is not total batch completion time divided by query count. Throughput is calculated from batch wall time.

One worker, by group:

| Group | HPA mean (µs) | BiHPA mean (µs) | BiHPA time change | HPA p95 (µs) | BiHPA p95 (µs) |
|---|---:|---:|---:|---:|---:|
| Short | 71.188 | 63.669 | −10.6% | 231.104 | 170.396 |
| Medium | 150.350 | 122.421 | −18.6% | 362.771 | 304.605 |
| Long | 214.169 | 248.674 | +16.1% | 485.209 | 553.187 |

## Expansions and diagnostics

A separate instrumented pass, once per query, is not used as the main latency measurement:

| Average per query | HPA | BiHPA |
|---|---:|---:|
| Vertex expansions | 1081.559 | 924.671 |
| Forward | 1081.559 | 487.710 |
| Backward | 0 | 436.961 |
| Edges examined | 4327.734 | 3739.372 |
| Stale heap entries discarded | 179.400 | 141.724 |
| Shortcuts on returned path | 11.786 | 11.804 |
| Peak heap, averaged across queries | 297.262 | 325.506 |
| Complete-path updates | — | 1.574 |
| Search instrumented (µs) | 187.413 | 193.218 |
| Instrumented path concatenation (µs) | 3.695 | 4.209 |

These are **expansion events**, not unique NodeIds: a vertex can reopen or be visited from both directions. Fewer expansions do not necessarily mean less time; BiHPA computes two heuristics per priority, manages two heaps, and checks meeting points. The counters show that savings in search steps are offset by other costs; no CPU profile attributes all overhead to a particular helper. No micro-optimization or retuning was performed after viewing this evaluation suite.

## Index, workspace, and setup

One-worker measurements, outside the query timer:

| Metric | HPA | BiHPA |
|---|---:|---:|
| Total index build (ms) | 15.809 | 16.248 |
| Reverse CSR build only (ms) | 0 | 0.432 |
| Total index (bytes) | 3,187,800 | 4,697,784 |
| Total index (MiB) | 3.040 | 4.480 |
| Workspace per worker (bytes) | 884,052 | 1,717,928 |
| Workspace per worker (MiB) | 0.843 | 1.638 |
| Stored shortcut paths (bytes) | 2,466,740 | 2,466,740 |
| NodeIds stored for shortcuts | 588,573 | 588,573 |
| Graph load (ms) | 119.837 | 115.818 |
| Router setup (ms) | 15.843 | 16.280 |
| Peak RSS, 1 worker (MiB) | 15.188 | 15.281 |
| Peak RSS, 4 worker (MiB) | 15.969 | 20.328 |

The reverse structures add 1,509,984 bytes = 1.440 MiB; shortcut paths are stored only once. Workspace nearly doubles because two search states are needed. Index/workspace bytes sum vector capacities, excluding allocator overhead/object headers. Peak RSS covers the entire process, including graph and retained results; do not add/subtract RSS to infer index size. Time-component medians are calculated independently and need not sum to the median total.

There is no reliable preprocessing break-even point relative to HPA: the suite-wide query advantage is smaller than measurement noise. Do not use the 0.082 µs/query difference to infer a falsely precise break-even query count.

## Path quality

All 240 measured batches and 2 diagnostic-pass batches pass the path checker and `w × optimum` bound (numerical tolerance of 0.000001 percentage points). Results within each algorithm are byte-identical across all processes, repetitions, and worker counts.

| Metric over 1,000 distinct queries | HPA | BiHPA |
|---|---:|---:|
| Valid paths | 1,000/1,000 | 1,000/1,000 |
| Matching optimum within checker tolerance | 901/1,000 | 886/1,000 |
| Mean gap | 0.005639% | 0.029488% |
| p50 gap | 0% | 0% |
| p95 gap | 0.014112% | 0.065208% |
| Max gap | 1.251586% | 1.622314% |
| Maximum extra distance | 115.330 m | 1059.246 m |

Both meet p95 ≤ 1% and maximum ≤ 5% on this suite. These are empirical results for a fixed configuration, not a p95 guarantee for every workload. Bidirectional Weighted search need not select the same path as unidirectional Weighted search.

| Group | HPA p95 gap | BiHPA p95 gap | HPA max gap | BiHPA max gap |
|---|---:|---:|---:|---:|
| Short | 0.020435% | 0.001978% | 1.251586% | 1.041773% |
| Medium | 0.025794% | 0.109181% | 0.270588% | 1.622314% |
| Long | 0.005343% | 0.135708% | 0.255491% | 1.592922% |

## Reproduction and verification

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j 4
ctest --test-dir build --output-on-failure
.venv/bin/python scripts/compare_bihpa.py \
  --output-dir artifacts/bihpa-comparison-new \
  --processes 6 --repetitions 10 --warmup 2 --threads 1 4 \
  --hpa-cluster-size 3500 --hpa-weight 1.05
```

- [Summary JSON stored in the repository](../benchmarks/bihpa-comparison.json): hashes, configuration, run schedule, timing by group, quality, and diagnostics.
- [Raw report](../artifacts/bihpa-comparison-20260918/report.json), [summary CSV](../artifacts/bihpa-comparison-20260918/summary.csv), and [manifest](../artifacts/bihpa-comparison-20260918/manifest.json). The session also contains a binary snapshot, timing CSVs, individual result files, checker output, and validation logs. `artifacts/` is ignored by Git; the script can reproduce a new session.
- Binary SHA-256: `55c0c8abf8b10ddc112f3f5794959df2edb973898888b458cae5d0edf76e32d9`.
- HPA output SHA-256 still matches the previous version: `d08391bc3e560de98d3809cc28dd549c5ee3f418899e928a2c3eda99d7368a5a`.
- BiHPA output SHA-256: `8dfd7c65035781ad033b5e868a4875c647924963809a6137b3033aad561563b6`.
- Tests: all 7 CTest suites; all pairs on 100 random graphs using Floyd–Warshall; termination/reopening/reverse-path expansion fixtures; CLI and multiple producers/workers; 4 C++ ASan/UBSan suites; 26 Python unittest tests, with 3 CLI classes run separately through CTest.

The new command is `--algorithm bihpa`. The design, priority formulas, and termination condition are explained in [BIHPA_DESIGN.md](BIHPA_DESIGN.md).
