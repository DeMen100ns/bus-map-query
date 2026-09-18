# BusMap — C++ shortest-path workspace

BusMap is a C++ pathfinding project that compares shortest-path algorithms on a distance graph built from Ho Chi Minh City (HCMC) bus route geometry. Python prepares and verifies the data; C++ implements Dijkstra, A*, HPA*, and bidirectional HPA*.

**Available:** a canonical graph, text exporter, C++ CSR graph and loader, a multi-query CLI with an optional worker pool, 1,000 queries with Python reference answers, optimal/any checking, timing benchmarks, and tests.

**Algorithms:** Dijkstra/A*, Weighted HPA*, and bidirectional Weighted HPA* are implemented. HPA uses Dijkstra to precompute shortcut paths, Weighted A* to search for a route, and stored paths to reconstruct it. Workers share an immutable index; query results are not cached. BiHPA implements bidirectional search in a separate file and shares HPA's shortcut paths. See the [HPA* design](docs/HPA_DESIGN.md), [BiHPA design](docs/BIHPA_DESIGN.md), [performance comparison](docs/BIHPA_RESULTS.md), and [HPA tuning results](docs/HPA_RESULTS.md).

## Latest recorded benchmark results — 2026-09-18

The workload contains **1,000 fixed queries** on the HCMC graph (**38,148 vertices, 42,170 directed edges**). Measurements use Release builds with AppleClang 14.0.3 and ThinLTO on macOS arm64 (8 logical CPUs), with queue capacity 64. HPA*/BiHPA* use **3,500 m clusters and weight 1.05**, stored shortcut paths, per-query heuristic caching, and a shared immutable index.

These are the latest saved measurements, from two sessions:

- **Dijkstra baseline and reused-workspace Dijkstra:** the later [Dijkstra variant comparison](docs/DIJKSTRA_VARIANTS.md), with 4 independent processes per configuration, 2 warm-up batches and 5 measured batches per process. These replace the older Dijkstra figures from the all-algorithm run.
- **A*, HPA*, and BiHPA*:** the [all-algorithm comparison](docs/ALGORITHM_BENCHMARK_20260918.md), with 4 independent processes per configuration, 2 warm-up batches and 10 measured batches per process.

Each value below is a median across processes. The sessions were run separately on a shared desktop with substantial timing variation, so cross-session differences are descriptive rather than a controlled comparison of the current implementations.

### One worker

A single worker calls the router directly and processes queries sequentially.

| Algorithm | Mean service (µs/query) | p95 service (µs/query) | Batch of 1,000 queries (ms) | Throughput (queries/s) |
|---|---:|---:|---:|---:|
| Dijkstra baseline | 1,720.701 | 3,523.646 | 1,720.908 | 612.7 |
| Dijkstra (reused workspace) | 1,327.377 | 2,550.229 | 1,327.437 | 755.5 |
| A* | 648.079 | 2,402.604 | 648.117 | 1,553.4 |
| Weighted HPA* | 170.712 | 497.020 | 170.745 | 6,066.3 |
| Bidirectional Weighted HPA* | 197.479 | 626.645 | 197.523 | 5,561.3 |

### Four workers

Four workers process different queries concurrently. Each query still runs on one worker; the graph and HPA indexes are shared, while search workspaces are private to each worker.

| Algorithm | Mean service (µs/query) | p95 service (µs/query) | Batch of 1,000 queries (ms) | Throughput (queries/s) | Throughput vs. 1 worker |
|---|---:|---:|---:|---:|---:|
| Dijkstra baseline | 1,874.228 | 3,719.333 | 470.470 | 2,125.9 | 3.47× |
| Dijkstra (reused workspace) | 2,117.961 | 4,393.750 | 531.431 | 1,883.7 | 2.49× |
| A* | 1,043.409 | 3,874.417 | 262.298 | 4,377.2 | 2.82× |
| Weighted HPA* | 226.053 | 652.792 | 57.123 | 18,850.2 | 3.11× |
| Bidirectional Weighted HPA* | 230.425 | 729.770 | 58.301 | 18,391.3 | 3.31× |

### Reading the results

- **Mean service** is the average time spent executing a query on a worker, including workspace allocation/reset, search, and full path reconstruction. It excludes queue wait. **p95 service** is the 95th percentile: about 95% of calls in each process finish within that service time.
- **Batch time** covers submitting all 1,000 queries and collecting their results. **Throughput** is queries completed per second, calculated from batch wall time. Loading the graph, building indexes, writing files, and checking results are excluded. Each metric is aggregated independently, so the displayed median throughput need not equal 1,000 divided by the displayed median batch time converted to seconds.
- **Four workers improve total throughput**, even when individual service times rise under resource contention and scheduling. The scaling column compares each algorithm's 4-worker and 1-worker throughput within its own benchmark session; it does not measure a speedup for a single query.
- **A* guides search toward the target with a distance heuristic. HPA* additionally uses precomputed shortcuts through intermediate clusters**, reducing query-time search work. That preprocessing is paid before queries: the all-algorithm run reports roughly 18 ms to build either hierarchical index, with about 3.04 MiB for HPA* and 4.48 MiB for BiHPA*, shared across workers.
- **HPA* has the highest recorded throughput in these tables.** BiHPA* searches from both ends, but managing two search frontiers and their meeting condition adds work; bidirectional search is not automatically faster. Process variation was large, so the small four-worker HPA*/BiHPA* difference is not a stable performance ranking.
- **Workspace reuse does not guarantee a whole-query speedup.** The latest reused Dijkstra mean is lower than baseline with one worker but higher with four; an earlier trial gave the opposite one-worker result. The [variant report](docs/DIJKSTRA_VARIANTS.md) explains the reset costs and measurement variability.

### Path quality

All measured batches passed their respective checkers. Dijkstra (both variants) and A* return optimal distances within numerical tolerance on this suite. HPA*/BiHPA* use Weighted search, trading exact optimality for query speed. With `w=1.05`, their theoretical cost bound is 5% above optimum under the documented heuristic assumptions; the observed gaps below are smaller.

| Algorithm | Valid paths | Optimal paths within tolerance | p95 distance gap | Maximum distance gap |
|---|---:|---:|---:|---:|
| Dijkstra (both variants) | 1,000/1,000 | 1,000/1,000 | 0.000000% | 0.000000% |
| A* | 1,000/1,000 | 1,000/1,000 | 0.000000% | 0.000000% |
| Weighted HPA* | 1,000/1,000 | 901/1,000 | 0.014112% | 1.251586% |
| Bidirectional Weighted HPA* | 1,000/1,000 | 886/1,000 | 0.065208% | 1.622314% |

Distance gap is `(returned distance / optimal distance - 1) × 100%`; it measures extra path length. Quality counts each of the 1,000 distinct queries once. For each algorithm, results are identical across worker counts and repeated runs, so using four workers changes throughput without changing path quality. Floating-point gaps near zero are rounded to zero above.

For process ranges, memory measurements, and reproducibility details, see the [all-algorithm report](docs/ALGORITHM_BENCHMARK_20260918.md) and [Dijkstra variant report](docs/DIJKSTRA_VARIANTS.md). The underlying data is in [algorithms-20260918.json](benchmarks/algorithms-20260918.json) and [dijkstra-variants-20260918.json](benchmarks/dijkstra-variants-20260918.json).

## Where to start

1. Read the [architecture and interfaces](docs/ARCHITECTURE.md) and [HPA* design](docs/HPA_DESIGN.md).
2. Build in Release mode and run CTest as described below.
3. Run `scripts/bench.sh` to benchmark an algorithm, or `scripts/tune_hpa.py` to select a cluster size on a separate tuning set.

See also the [full report](PROJECT_REPORT.md), [JSON schema](docs/DATA_FORMAT.md), [data sources](docs/SOURCES.md), and [query suite](benchmarks/README.md).

## Directory structure

```text
BusMap/
├── CMakeLists.txt
├── data/raw/                    # Original snapshot, unchanged
├── data/processed/              # graph.json, graph.txt, manifest
├── python/preprocessing/        # Normalization, validation, export
├── python/reference/            # Historical Python algorithms
├── python/verification/         # Oracle, generator, checker
├── scripts/busmap_data.py       # Python entry point
├── cpp/include/busmap/          # Shared interfaces
├── cpp/src/                     # Loader/CSR/router and four algorithms
├── cpp/apps/                    # CLI
├── tests/                       # Python, C++, fixtures
├── benchmarks/                  # Fixed queries and reference answers
├── docs/
├── artifacts/                   # Generated as needed, ignored by Git
└── build/                       # CMake output, ignored by Git
```

## Environment and build

C++20, CMake >= 3.20, and Python >= 3.12 are required to build the data with `pyproj==3.8.0`. The C++ loader requires neither a JSON library nor pyproj. The Python oracle and checker use the standard library.

From the BusMap root directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DPython3_EXECUTABLE="$PWD/.venv/bin/python"
cmake --build build -j 4
ctest --test-dir build --output-on-failure
.venv/bin/python -m unittest discover -s tests -v
```

Release builds enable LTO by default through CMake IPO for the library and executables; CMake checks compiler/linker support before building. This option does not enable LTO for Debug builds. Disable it with `cmake -S . -B build -DBUSMAP_ENABLE_LTO=OFF`, then rebuild; use `ON` to re-enable it. Benchmarks record the setting in `timing.json.lto_enabled`.

`unittest discover` skips classes that require executables because CTest supplies the binary paths. CTest checks the loader/dataset, Weighted HPA, worker pool, streaming CLI, and Dijkstra/A*/HPA/BiHPA results on 1,000 HCMC queries in both sequential and parallel modes.

## Preparing the data

The files have already been generated. Rebuild only when needed:

```sh
.venv/bin/python scripts/busmap_data.py build
.venv/bin/python scripts/busmap_data.py export
.venv/bin/python scripts/busmap_data.py queries
```

- `build`: raw data → canonical JSON v1.
- `export`: JSON → C++ text format and manifest.
- `queries`: generate 1,000 queries and Python Dijkstra answers, with default seed `162163`.

Default paths are relative to the script location and do not depend on the working directory. You can invoke the script by absolute path from elsewhere; paths supplied through options are relative to your working directory.

## Running and checking C++

```sh
mkdir -p artifacts
./build/busmap-query --graph data/processed/graph.txt --algorithm dijkstra \
  --queries benchmarks/queries.txt > artifacts/results.txt
.venv/bin/python scripts/busmap_data.py check --results artifacts/results.txt
```

All four algorithms return actual paths for the checker to validate. Dijkstra has two variants: `dijkstra` reuses its workspace/heap and resets touched vertices; `dijkstra_baseline` allocates new distance, parent, and heap storage for each call. `--algorithm` accepts `dijkstra`, `dijkstra_baseline`, `astar`, `hpa`, and `bihpa`. HPA and BiHPA share the `--hpa-cluster-size` and `--hpa-weight` options. See the [two Dijkstra variants](docs/DIJKSTRA_VARIANTS.md).

Queries can also be supplied through stdin; the graph is still loaded exactly once:

```sh
./build/busmap-query --graph data/processed/graph.txt < benchmarks/queries.txt
```

Check edge cases with the small fixture:

```sh
./build/busmap-query --graph tests/fixtures/graph.txt \
  --queries tests/fixtures/queries.txt > artifacts/fixture-results.txt
.venv/bin/python scripts/busmap_data.py check --graph tests/fixtures/graph.json \
  --queries tests/fixtures/queries.txt --answers tests/fixtures/answers.json \
  --results artifacts/fixture-results.txt
```

The fixture contains a query with an invalid vertex ID: the CLI returns exit code 1 for that batch even when the other queries are processed correctly. The checker accepts the result when the corresponding line reports `invalid_vertex`.

### Running multiple queries concurrently

```sh
cmake --build build --parallel 4
./build/busmap-query --graph data/processed/graph.txt --algorithm astar \
  --queries benchmarks/queries.txt --threads 4 --queue-capacity 64 \
  > artifacts/astar-parallel.txt
python3 scripts/busmap_data.py check --results artifacts/astar-parallel.txt --mode optimal
```

`--threads` defaults to 1 (sequential execution); a larger value creates exactly that many fixed workers. Each worker retains a Router/workspace across queries and shares the read-only graph. The queue is bounded, with a default capacity of 64 pending jobs. The result buffer is also bounded so that it does not retain every path in RAM. When full, input reading waits for workers/output to free space.

Results remain in input order; query IDs do not need to increase. Stdin is processed continuously: the first result can arrive before all queries or EOF have been sent. When an early query is slow, later results wait for their output turn. A dedicated output thread is added; the number of pathfinding workers still equals `--threads`. There is no shared result cache.

Focused tests: `ctest --test-dir build -R 'query_pool|concurrency' --output-on-failure`.

### Two result-checking modes

```sh
python3 scripts/busmap_data.py check --results artifacts/results.txt --mode optimal
python3 scripts/busmap_data.py check --results artifacts/results.txt --mode any
```

- `optimal` (default): a valid path whose distance matches the oracle optimum within tolerance.
- `any`: accepts any valid path, including one longer than the optimum; still checks edge direction, endpoints, total weight, and reachability.
- Both report the distance gap. When the optimum is 0 but the returned path is longer, any mode still accepts it; the percentage is null and the extra distance is reported in meters.
- `not_implemented`, invalid paths, and incorrect unreachable statuses fail in both modes. Source=target still requires a singleton path.
- `exact` and `approximate` remain aliases for `optimal` and `any`.

The mode is a checker criterion; it does not change the algorithm, heuristic, or search termination. Comparing the performance of two search strategies requires running their respective implementations; the commands above only assess result quality.

## Timing benchmarks

The quickest option is the shell script, which builds in Release mode, benchmarks **one** algorithm, and runs the checker:

```sh
./scripts/bench.sh dijkstra
./scripts/bench.sh dijkstra_baseline --threads 4
./scripts/bench.sh astar --mode any
./scripts/bench.sh astar --threads 4 --queue-capacity 64
./scripts/bench.sh hpa --repetitions 3 --warmup 1
```

Defaults are any mode for HPA, optimal for Dijkstra/A*, 5 measured runs, and 1 warm-up run. The script uses Python from .venv when available, otherwise python3 on PATH. It can be invoked by absolute path from another directory. Reports are generated under artifacts/benchmarks; use --output-dir to select a new directory. The exit code is nonzero if the build or check fails. See `./scripts/bench.sh --help`.

Build in Release mode and run the same query suite for Dijkstra/A*:

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --target busmap-bench -j 4
python3 scripts/benchmark.py --algorithm dijkstra astar --mode optimal
```

Defaults: one warm-up pass over all queries, five measured passes, and one thread. Add `--threads 4 --queue-capacity 64` to benchmark the worker pool (supported by `bench.sh`, `benchmark.py`, and `busmap-bench`). One executor is retained throughout warm-up and measurement. Each algorithm runs in a separate process and loads the graph once. `--algorithm astar` measures only A*; `--mode any` changes the verification criterion. You can set `--repetitions 5 --warmup 1` and `--output-dir <new directory>`.

Reports are written to `artifacts/benchmarks/<session>/report.md` and `report.json`. They include worker count/queue capacity, load/setup time, mean/p50/p95 for service, wait, and latency, batch wall time, and throughput (queries/second). CSV files retain every sample; each run has a result file and checker report. All measured results are verified **outside** the query timing interval. Runs that fail the criterion are marked FAIL; stubs are not benchmarked as successful algorithms.

The C++ `steady_clock` measures:

- **Service:** time spent in `router.query()` on a worker, including search, internal allocations, and path reconstruction. This is wall time, not CPU time.
- **Wait:** from the start of submission until the worker starts the query; includes submission overhead, waiting for a full queue, and scheduling.
- **Latency:** from the start of submission until the search finishes; excludes transferring results through a future or waiting for CLI output order.
- **Batch wall:** from submitting the first query until all results are collected, including scheduling/result collection; excludes load/setup, allocating result arrays before the batch, verification, file writes, and destroying results after the batch.
- **Throughput:** total measured queries divided by total batch wall time. It is not divided by the sum of service times for parallel queries.

One thread retains direct calls (wait=0); multiple threads use the actual QueryPool. The benchmark pushes a finite batch through the bounded queue as quickly as possible and retains all paths until the end of the batch for writing/checking. This is a saturated workload; it does not simulate user query arrival rates. Run `--threads 1` and `--threads 4` separately with identical queries/warm-up/repetitions to compare throughput and batch wall time; mean service time is not the processing rate for the whole batch.

Example:

```sh
./scripts/bench.sh astar --threads 1
./scripts/bench.sh astar --threads 4
```

CSV schema v2 retains `duration_ns` for service and adds `wait_ns` and `latency_ns`. `run_query_total_ms` remains the sum of service times, while `run_batch_wall_ms` is the wall time of each batch. Graph loading is measured once without clearing the OS file cache; this is not a cold-disk measurement. Dijkstra/A*/HPA have no result cache; HPA reuses its distance index.

The optimal/any mode does not change the pathfinding algorithm; the same implementation still performs the same work. Results depend on the machine, background load, and query suite. Percentiles use nearest-rank over all queries from measured runs.

Focused benchmark infrastructure tests: `ctest --test-dir build -R '^benchmark$' --output-on-failure`. Tests check metrics, results across multiple runs, and the pool with 1/2/4 threads; concurrent throughput is derived from batch wall time.


## Weighted HPA* and tuning

```sh
./scripts/bench.sh hpa
./scripts/bench.sh hpa --hpa-cluster-size 3500 --hpa-weight 1.05 --threads 4
.venv/bin/python scripts/tune_hpa.py --output-dir artifacts/hpa-tuning --budget-minutes 60
```

HPA retains only **Weighted search with stored shortcut paths**, defaulting to **L=3,500 m, w=1.05**. It always reuses a private workspace per worker and shares an immutable index. `--hpa-weight` must exceed 1; the workspace option has been removed. Dijkstra precomputes both shortcut distances and paths; queries read and concatenate the stored paths. HPA benchmarks default to the `any` checker and report error relative to the oracle; `--mode` does not change the algorithm.

The Weighted configuration achieved a p95 gap of 0.0141% and a maximum gap of 1.2516% on the evaluation suite. See the [current design](docs/HPA_DESIGN.md), [historical tuning report](docs/HPA_RESULTS.md), and [measured workspace lifecycle comparison](docs/HPA_WORKSPACE_COMPARISON.md). Older reports retain exact/fresh measurements for reference; both modes have been removed from the current code.

Tuning uses 1,000 queries whose sources are disjoint from the evaluation suite, searches only `w>1`, and locks the configuration before held-out evaluation. Reports retain CSV/JSON, correctness, quality by distance group, build time, index bytes, workspace, and peak RSS. Python with matplotlib can generate charts using `scripts/tune_hpa.py --render-only <session>`.

### Storing shortcut paths during preprocessing

HPA always stores the full path for each shortcut and concatenates it directly during queries. Run `./scripts/bench.sh hpa`; the `--hpa-path-storage` option no longer exists. The `refine` and `trees` branches and the comparison script have been removed from the code. The [historical comparison report](docs/HPA_PATH_STORAGE.md) retains the measurements used to select this storage method.

## Dataset and limitations

- 38,148 vertices and 42,170 directed edges; weights are in meters.
- 297 direction/variant records across 150 RouteId values in the historical snapshot.
- Vertices describe route geometry and do not necessarily represent bus stops.
- No schedules, waiting times, traffic, or transfer costs.
- The collection date is unknown; normalization does not update the snapshot to the current bus network.
- Historical Python A*/hierarchical implementations remain as references, including the issues documented in the report. The separate oracle is the reference for the query suite.
