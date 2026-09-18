# Architecture and guide to implementing C++ algorithms

## 1. Responsibilities

Python handles data and verification. C++ processes queries on the prepared graph. Dijkstra and A* have implementations written by you. HPA* retains only Weighted search with Dijkstra preprocessing and stored shortcut paths. The CLI supports a fixed worker pool; there is no result cache yet.

```text
raw JSONL → Python builder → canonical graph.json
                                  ├→ exporter → graph.txt → C++ loader → Graph CSR
                                  └→ oracle/generator → queries + answers          ↓
                                                            query → Router → your algorithm
                                                                        ↓
                                                               result → Python checker
```

The CLI does not call Python. Each process loads the graph once, then processes queries sequentially or through a worker pool with `--threads N`. File reading and result serialization belong to the CLI; algorithm functions work with data in RAM.

## 2. Graph and data types

`cpp/include/busmap/graph.hpp`:

- `NodeId = uint32_t`.
- `Coordinate {lat, lon, x_m, y_m}`; geographic coordinates in EPSG:4326, projected coordinates in EPSG:3405.
- `Edge {target, distance_m}`.
- `Graph::node_count()`, `edge_count()`, `graph_sha256()`.
- `Graph::coordinate(u)` returns a const reference; `outgoing(u)` returns `span<const Edge>`.

CSR consists of N+1 offsets and a contiguous edge array. Vertices without outgoing edges have an empty span. The public API does not allow graph mutation. The loader validates the file contract before the graph is used.

`cpp/include/busmap/query.hpp`:

- `Query {uint64_t id; int64_t source; int64_t target;}`. Signed input IDs allow negative values to be reported as `invalid_vertex`.
- `PathResult {PathStatus status; optional<double> distance_m; vector<NodeId> path;}`.
- `Found`: a finite, nonnegative distance and a path whose endpoints match the query.
- `Unreachable`, `InvalidVertex`, `NotImplemented`: distance is `nullopt`, and the path is empty.
- A valid query with source equal to target: the algorithm returns `Found`, 0, `[source]`.

Router holds a reference to the graph, which must outlive it. Router validates IDs and dispatches queries; it does not solve valid queries on behalf of the algorithm.

## 3. Algorithm entry points

Functions are declared in `cpp/include/busmap/{dijkstra,dijkstra_baseline,astar,hpa,bihpa}.hpp` and implemented in `cpp/src/`. The baseline is separate in `Dijkstra_baseline.cpp`:

```cpp
PathResult dijkstra(const Graph& graph, const Query& query, SearchWorkspace& workspace);
PathResult dijkstra_baseline(const Graph& graph, const Query& query);
PathResult astar(const Graph& graph, const Query& query, SearchWorkspace& workspace);
PathResult hpa(const Graph&, const Query&, const HpaIndex&, HpaWorkspace&,
               double heuristic_weight = 1.05, HpaDiagnostics* = nullptr);
PathResult bihpa(const Graph&, const Query&, const BiHpaIndex&, BiHpaWorkspace&,
                 double heuristic_weight = 1.05, BiHpaDiagnostics* = nullptr);
```

Traversing the data:

```cpp
for (const auto& edge : graph.outgoing(u)) {
    // edge.target, edge.distance_m
}
```

### Workspace for Dijkstra and A*

`cpp/include/busmap/search_workspace.hpp` defines `SearchWorkspace` with `distance`, `parent`, `touched`, and `dijkstra_heap` vectors. Each Router owns a workspace that starts empty and persists across queries. The graph remains read-only. The sequential CLI and benchmark retain the router outside the query loop; each worker has its own router.

`dijkstra` initializes the full arrays when their size changes; subsequent calls reset only the distances of vertices in `touched`. Parents are overwritten when vertices are discovered; the source parent is reset to a sentinel. The source and every discovered vertex are recorded. The heap uses a workspace vector; each query clears entries left by the previous search while retaining capacity. Heap ordering is `(distance, NodeId)` for deterministic tie-breaking. Reset occurs before every valid query, including source equal to target.

`dijkstra_baseline` in `Dijkstra_baseline.cpp` does not take a workspace. It allocates new local distance/parent vectors and a priority queue for every valid query; search resources are destroyed on return. The graph is still shared. This variant supports multiple workers because it has no shared search state.

A* retains its existing behavior: reusable distance/parent/touched arrays and a local priority queue. Paths from both Dijkstra variants and A* own their vectors, so later queries do not change earlier results. See [DIJKSTRA_VARIANTS.md](DIJKSTRA_VARIANTS.md) for a comparison.

A workspace is working memory, not a result cache. Do not call the same Router concurrently. In QueryPool, each worker has its own Router/workspace and shares the same read-only Graph. HPA has a private HpaWorkspace per Router and a shared shared_ptr<const HpaIndex>.

HpaIndex persists across queries and is built synchronously once during Router/QueryPool setup. The pool builds the index before spawning workers, not inside each `hpa()` call. The index's graph and cluster size are checked when it is passed to a Router/pool. The index always stores shortcut paths; query results are not cached. See [HPA_DESIGN.md](HPA_DESIGN.md) for the algorithm, heuristic, ownership, and tuning. BiHPA uses `BiHpaIndex`, which shares the base HPA index and adds incoming-edge CSR; `BiHpaWorkspace` contains two search workspaces. This implementation is separate in `bihpa.cpp`; see [BIHPA_DESIGN.md](BIHPA_DESIGN.md).

The x/y coordinates support heuristics in meters; A* currently uses Euclidean distance on projected coordinates. General fixtures have manually chosen weights rather than coordinate distances; a geographic heuristic is not automatically admissible on them. A zero heuristic can be used for initial correctness checks. For real data, verify the metric assumptions before using a heuristic.

## 4. CLI lifecycle and verification

The `busmap-query` CLI reads the query header, then processes each line without waiting to collect all queries. The default `--threads 1` runs sequentially. With multiple workers, results are written/flushed in input order when their turn arrives. Stdin still requires a count header and EOF after the last record.

### Worker pool and memory lifecycle

`QueryPool` (`cpp/include/busmap/query_pool.hpp`, `cpp/src/query_pool.cpp`) creates N pathfinding threads once. Each thread constructs a Router on its stack before entering the job loop, so the workspace persists until the worker exits. Workspaces are reused across queries; HPA retains only Weighted search, with default L=3500 m and w=1.05, and has no mode that creates a fresh workspace per call. The graph must outlive the pool.

`submit(Query)` returns `future<PathResult>` and supports multiple producers. A mutex and condition variables protect the bounded queue; pathfinding runs outside the lock. `close()` stops accepting new jobs, wakes waiting producers, and lets accepted jobs finish. The destructor joins every worker. Algorithm exceptions propagate through futures rather than escaping worker threads. Running algorithms are not cancelled mid-query.

The CLI also has an output thread (`cpp/apps/ordered_results.hpp`). It consumes futures in input order while the main thread continues reading stdin. Both the job and future queues have capacity `--queue-capacity` (default 64); outside the future queue, at most one result is being written and one producer submission is waiting to enter it. These bounds prevent paths from accumulating with the total query count. Graph memory and the ID list used to detect duplicate queries remain outside this bound.

Waiting for earlier queries follows from preserving output order. Each worker processes its jobs sequentially and needs no workspace lock. The pool does not share a result cache. HPA shares an immutable index and keeps private search state per worker.

Exit codes: 0 when all queries are processed (found/unreachable); 1 for input/I/O errors or invalid_vertex; 2 for not_implemented. Invalid input discovered mid-batch may leave incomplete output, which the checker rejects. Diagnostics go only to stderr.

There are two verification layers:

1. CTest checks CSR/loading, CLI behavior, the worker pool, result ordering/streaming, pool shutdown, and exceptions through futures. Dijkstra/A*/HPA are checked against the HCMC oracle; HPA is also verified for all pairs on 100 small graphs using Floyd–Warshall.
2. The Python checker compares actual paths with oracle answers; it does not require identical paths when multiple solutions tie.

Python answers are ground truth for this snapshot; they do not establish the real-world correctness of the bus network. Oracle/checker time must not be reported as C++ algorithm time.

## 5. Python components

`preprocessing` retains JSON v1 rules: exact coordinates, dense IDs, directed edges, removed self-loops, and minimum-weight merging of duplicate edges. `reference` retains the old code with only imports and paths changed. Run it from the root with `PYTHONPATH=python python3 -m reference.dijkstra`; this script remains a historical benchmark, not the new benchmark.

`verification.oracle` is an independent heap-based Dijkstra implementation, additionally verified with Floyd–Warshall on small graphs. `verification.generate` uses the oracle to build the query suite. `verification.check` uses canonical JSON to verify edges and total weights.

All data commands go through `scripts/busmap_data.py`. Generated files are written to temporary files and then replaced; manifests are written after data files. This is atomic per file, not a multi-file transaction. If interrupted, rerun and check hashes before use.

## 6. Timing benchmark

`busmap-bench` uses the same Router/Graph/QueryPool/algorithms as the CLI. `--threads 1` calls the router directly; multiple threads use a fixed pool with configurable queue capacity. The executor persists throughout warm-up and measurement. `QueryPool::submit_timed()` returns a future containing both PathResult and QueryTiming; the CLI's `submit()` API does not read the clock.

QueryTiming contains `duration_ns` (worker execution of the router), `wait_ns` (submission start to worker start), and `latency_ns` (submission start to query completion). Timestamps are recorded before publishing results through the promise, so latency excludes future retrieval and output ordering. CSV schema v2 stores all three intervals. Times are wall-clock measurements, including periods when the OS suspends a thread.

The benchmark allocates result arrays before the batch, starts the batch timer, submits through the bounded queue, and collects all futures. Validation and result writing occur after stopping the timer. Each batch retains all paths in RAM, unlike the CLI's bounded output buffer. `run_query_total_ms` is total service time (which can exceed wall time under parallel execution). `run_batch_wall_ms` is wall time from dispatch until all results are collected. Throughput = total queries / total batch wall time (seconds). Graph loading and router/pool creation are reported separately, outside the batch timer.

This is a finite saturated batch benchmark; it does not simulate arrival rates/HTTP behavior in an online system. Queries not yet submitted have not started their latency timers; queue capacity affects wait time and throughput. Reading graph/queries, checking, writing files, and destroying results after the batch are all outside the batch timer.

`scripts/benchmark.py` and `scripts/bench.sh` accept `--threads` and `--queue-capacity`. Each algorithm runs in a separate process with the selected number of workers. The runner summarizes service/wait/latency, batch wall time, and throughput, checking every run in optimal/any mode. Report schema v2 distinguishes timing from correctness; algorithm source is unchanged. Measurements do not include an HTTP endpoint.
