# Fixed HCMC query suite

1,000 distinct source/target pairs, all reachable with source != target, seed 162163. This is a synthetic workload for verification and future benchmarking; it can be measured with the C++/Python runner below.

## Generation method

1. Shuffle all 38,148 IDs using Python's RNG with a fixed seed.
2. For each source, run the Dijkstra oracle and randomly select up to 10 reachable targets other than the source.
3. Stop at a pool of 3,000 pairs; report an error if the dataset cannot provide enough.
4. Sort the pool by `(distance,source,target)` and split it into 3 groups of 1,000.
5. Select 334/333/333 pairs from the groups, then shuffle the final order.

“Short/medium/long” are tertiles within the pool, not exact quantiles of all graph pairs. Sampling is not uniform over all reachable pairs and does not represent real user traffic. Group labels and distances are in answers.json; ranges and hashes are in manifest.json.

## Regeneration

From the root: `.venv/bin/python scripts/busmap_data.py queries`. Options include `--seed`, `--graph`, and `--output-dir`. The same tested Python/environment and input produce byte-identical output. Current timestamps are not embedded in deterministic content.

`queries.txt` is for C++; `answers.json` is for the checker; `manifest.json` contains configuration, hashes, and statistics. Optimal paths are not stored in answers: the checker accepts different paths of equal length.

The small `tests/fixtures` suite adds unreachable pairs, invalid IDs, source=target, isolated vertices, zero-weight edges, and tied paths. valid_results.txt contains fixture results from the Python oracle, not C++ results.

For future performance benchmarks, use the same workload, load the graph before timing, disable result caching to measure the algorithm first, and measure preprocessing separately. The timing runner supports a worker pool; there is no query-result cache yet.

## Two verification criteria

Use `scripts/busmap_data.py check --mode optimal` to require shortest paths, or `--mode any` to require only valid paths. Both modes still validate reachable/invalid statuses and reject stubs. Any mode accepts output longer than the optimum and reports gaps to assess quality.

The mode does not change the algorithm. A single result file can be assessed with both criteria without rerunning the algorithm.

## Timing runner

`dijkstra` now uses a reusable workspace/heap and resets touched vertices;
`dijkstra_baseline` creates new resources per query. Both use the `optimal` checker.
Metadata `timing.json.dijkstra` specifies `resource_policy`, `distance_reset`, and `heap_storage`.
[Design and measurements](../docs/DIJKSTRA_VARIANTS.md).
Reports predating this change retain measurements from the earlier implementation.

Measurements of Dijkstra, A*, HPA, and BiHPA with 1/4 workers, Release ThinLTO:
[2026-09-18 report](../docs/ALGORITHM_BENCHMARK_20260918.md),
[summary JSON](algorithms-20260918.json). Each configuration has 4 independent processes;
the report retains all measurements and their ranges.

From the root, run `python3 scripts/benchmark.py --algorithm dijkstra astar --mode optimal` after building the busmap-bench target in Release mode. Defaults: warmup=1 batch, repetitions=5 batches. Graph and queries are read before warm-up; graph loading/router setup are recorded separately.

C++ times each router.query call using steady_clock, including workspace allocation and path reconstruction. Batch results remain in RAM and are serialized after timing ends; result destruction is outside the measurement. Retaining paths can affect RAM/allocator behavior in later calls, so use the same workload and runner for comparisons.

Python computes mean, p50, p95, and max from timings.csv, using nearest-rank percentiles, and checks every run. mean_batch_query_ms is the mean total query time per batch, excluding I/O and checking. report.json records the environment, compiler, build type, binary/data/query/answer hashes, and verification results.

Release enables LTO by default (`-DBUSMAP_ENABLE_LTO=ON` in CMake). `timing.json`
and algorithm entries in `report.json` record `lto_enabled`; use `OFF` and rebuild
for comparison. The current compiler uses ThinLTO; CMake selects toolchain-appropriate flags.
See the [LTO on/off measurements](../docs/LTO_RESULTS.md).

An incorrect algorithm may still produce timings but is marked FAIL; do not interpret it as a faster correct solution. Stubs are rejected. Algorithms run sequentially in name order; background load/thermal conditions may affect results. Graph loading is measured once and is affected by the OS page cache. Repetitions run within one process per algorithm and do not represent multiple cold starts.

If solver caching is added later, define reset/cache-hit policies before using this runner to make cache-performance claims; the runner currently does not reset internal solver state.

## HPA tuning

`scripts/tune_hpa.py` retains this suite as held-out, generates a tuning suite with seed 162164 within the session, and excludes every source present here. The Dijkstra oracle, short/medium/long distances, and hashes are stored separately. Existing queries/answers/manifest files are not overwritten.

Tuning selects L by the median of mean full-query service across processes, checking every batch. HPA searches only Weighted configurations with `w>1`: paths must be valid, satisfy the `w` bound, and meet p95 gap ≤1% and maximum gap ≤5%. The configuration is saved to locked.json before held-out measurement. Held-out results are not used for automatic retuning.

See the [design and protocol](../docs/HPA_DESIGN.md). `timing.json` schema v2 adds `hpa`, `peak_rss_bytes`, `max_hpa_workspace_bytes_per_worker`, and `diagnostics_enabled` metadata; the earlier timing CSV is unchanged.

## HPA workspace

Each worker always reuses its workspace; the index is built once and shared.
Heuristics are now cached by NodeId within each query: HPA uses first touch,
while BiHPA uses epochs for the h pair shared by both directions. Cached values do not remain
valid across queries. Metadata `hpa.heuristic_cache = per_query` and
`hpa.heuristic_cache_invalidation = first_touch|epoch` identify this behavior.
Diagnostics add `heuristic_requests` and `heuristic_evaluations`; workspace bytes
include cache vectors. [Cache report](../docs/HEURISTIC_CACHE_RESULTS.md).
`max_hpa_workspace_bytes_per_worker` measures the largest vector capacity in one worker,
not RSS. `scripts/bench.sh hpa` defaults to the `any` checker; Dijkstra/A* still default
to `optimal`. Explicitly select `--mode` to change the checker criterion.

The [workspace comparison report](../docs/HPA_WORKSPACE_COMPARISON.md) retains measurements
from the earlier version; fresh mode and the comparison script have been removed from current source.

## Shortcut path representation comparison

HPA retains only shortcut paths stored during preprocessing and concatenated during queries (`paths`).
There is no longer a path-representation option or a script comparing variants.
`timing.json.hpa.path_storage` is always `paths` to identify measurements;
`path_storage_bytes` and `path_nodes` report shortcut storage memory and NodeId counts.
Diagnostics use `search_ns`, `reconstruction_ns`, and `overlay_expanded`;
there is no longer an internal-search phase or a `local_expanded` counter.
See the [historical comparison report](../docs/HPA_PATH_STORAGE.md).

## Bidirectional HPA

`--algorithm bihpa` uses the same L/w and shortcut paths as `hpa`, adding bidirectional WBAE* search.
Both default to the `any` checker; the runner also checks the w bound in every batch.
For BiHPA, `timing.json.hpa.index_bytes` and `index_build_ms` include the base and reverse CSR;
`base_index_*` and `reverse_*` fields separate the two components.

Compare fixed configurations with `scripts/compare_bihpa.py --output-dir artifacts/bihpa-new`.
Defaults: 6 independent processes per algorithm/worker-count pair, 2 warm-ups + 10 measured batches,
1 and 4 workers, queue 64, balanced AB/BA with seed 162164. This query suite is not used for tuning.
See the [results](../docs/BIHPA_RESULTS.md) and [design](../docs/BIHPA_DESIGN.md).
