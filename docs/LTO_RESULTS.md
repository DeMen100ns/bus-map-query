# Release LTO — 2026-09-18

## Configuration

`BUSMAP_ENABLE_LTO` defaults to `ON`. CMake checks IPO with `CheckIPOSupported`,
then sets `CMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE` before creating targets.
The `busmap` library, CLI, benchmark, and Release test executables all use LTO.
If the toolchain lacks support, configuration fails with instructions for disabling the option.

On the measurement machine, AppleClang 14.0.3.14030022 uses `-O3 -DNDEBUG -flto=thin`
for both compilation and linking. CMake selects the LTO flags; Clang-specific flags are not
hardcoded into the project. The option enables IPO only for Release, not Debug/sanitizer builds.
`timing.json.lto_enabled` and algorithm entries in `report.json` record LTO status to distinguish builds.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DBUSMAP_ENABLE_LTO=ON
cmake --build build --parallel 4
ctest --test-dir build --output-on-failure
```

Replace `ON` with `OFF` and rebuild to disable it. Algorithms, heuristic caching, cluster size,
weight, and arithmetic remain unchanged; no fast-math or march=native flags are added.

## Measurement methodology

Both binaries are built from the same source with the same compiler, differing only in LTO ON/OFF.
Both use the graph with 38,148 vertices / 42,170 edges, `L=3500 m`, `w=1.05`, stored shortcut
paths, reusable workspaces, and per-query heuristic caching.

The tuning suite contains 1,000 queries, seed 162164, with sources disjoint from the evaluation suite.
Each algorithm/worker-count/build combination uses 4 independent processes, each with 2 warm-ups
and 8 measured batches. The 32 measurement processes run sequentially in balanced ON/OFF AB/BA
order with a fixed seed. Queue capacity is 64. Service time includes full path construction;
graph loading, index construction, writing, checking, and instrumentation are outside the query
timer. L/w are not reselected.

Summary values are medians across processes; p95 is calculated per process, then median-aggregated.
All processes are retained, with no outlier removal. Cores are not pinned and background load is
not isolated; small differences do not establish a stable benefit.

## Results

| Algorithm | Worker | Mean OFF → ON (µs) | Time change | p95 OFF → ON (µs) | Throughput OFF → ON (q/s) |
|---|---:|---:|---:|---:|---:|
| hpa | 1 | 137.477 → 139.454 | +1.44% | 399.125 → 405.146 | 7272.7 → 7169.3 |
| bihpa | 1 | 149.338 → 145.814 | -2.36% | 489.583 → 482.729 | 6696.8 → 6856.7 |
| hpa | 4 | 173.771 → 164.078 | -5.58% | 509.771 → 480.480 | 22736.2 → 24040.0 |
| bihpa | 4 | 222.980 → 225.566 | +1.16% | 716.708 → 728.062 | 17636.5 → 17412.8 |

One worker: HPA takes 1.44% more time, while BiHPA takes 2.36% less. Four workers:
HPA throughput increases 5.73%, while BiHPA decreases 1.27%. These are observed changes;
there is no evidence of a stable LTO query speedup for both algorithms.

Four-worker noise is substantial. BiHPA OFF process means range from 195.50–346.49 µs,
while ON ranges from 196.14–269.77 µs. All values and run order are in the JSON;
slow processes are not removed to improve the figures.

## Preprocessing and memory

Median index construction in one-worker processes, separate from query time:

| Algorithm | LTO OFF (ms) | LTO ON (ms) | Time reduction |
|---|---:|---:|---:|
| hpa | 15.733 | 14.212 | 9.67% |
| bihpa | 16.160 | 15.057 | 6.83% |

`busmap-bench` shrinks from 175,321 to 167,529 bytes (4.44%).
This is executable file size, not RSS or index size.

Index memory is unchanged: HPA 3,187,800 bytes, BiHPA 4,697,784 bytes.
Per-worker workspace on the tuning suite is unchanged: HPA 1,164,660 bytes,
BiHPA 2,480,888 bytes. Peak RSS for each process is recorded separately in JSON.
C++ build time was not measured; index construction time does not imply compile/link cost.

## Verification

- All 7/7 Release CTest suites pass; all 4/4 C++ ASan/UBSan suites pass in a separate Debug build.
- Compile/link checks: Release ON has ThinLTO, OFF does not; Debug with the option ON also has no LTO. Other flags match except for the ON/OFF metadata macro.
- Algorithm file and header hashes are unchanged. Every measured batch passes the checker and w bound; output is byte-identical across ON/OFF, repetitions, and worker counts.
- Separate diagnostic pass: expansions, edges examined, stale entries, shortcuts, heap peaks, and meeting points match for every query. Heuristic evaluation counts are also unchanged.
- The 1,000-query evaluation suite: OFF 1 batch, ON 2 batches. Paths are byte-identical and quality is unchanged. These runs verify correctness, not performance claims or configuration selection.

| Evaluation suite | Valid paths | Matching optimum | p95 gap | Max gap |
|---|---:|---:|---:|---:|
| hpa | 1000/1000 | 901/1000 | 0.014112% | 1.251586% |
| bihpa | 1000/1000 | 886/1000 | 0.065208% | 1.622314% |

## Reproduction

The [summary JSON](../benchmarks/lto-comparison.json) records hashes, flags, individual processes,
medians/ranges, diagnostics, quality, and build verification results.
The [raw session](../artifacts/lto-20260918-161414/tune/report.json) contains binaries, timing CSVs,
per-batch results/checker output, and logs; artifacts are ignored by Git.

Build one Release directory with `-DBUSMAP_ENABLE_LTO=OFF` and another with `ON`,
using the same compiler and other flags. Example with the OFF build at
`artifacts/build-no-lto/busmap-bench`:

```sh
.venv/bin/python scripts/compare_hpa_binaries.py \
  --before artifacts/build-no-lto/busmap-bench \
  --after build/busmap-bench \
  --queries artifacts/hpa-tuning-20260918/tune/queries.txt \
  --answers artifacts/hpa-tuning-20260918/tune/answers.json \
  --output-dir artifacts/lto-new \
  --processes 4 --repetitions 8 --warmup 2 --threads 1 4
```

Binary OFF: `81c401daa75b3796ab34a20135fc7ec0eea0c53d1e95b2f4abe6dccaea3e5c37`.
Binary ON: `cf873f7f92d22f2de16db96bd58e729f0bbf6b672d17386b856dafc9fc9b6a6b`.
