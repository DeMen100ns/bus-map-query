# BusMap — Project report

## 1. Overview

BusMap is a C++ pathfinding project for comparing shortest-path algorithms on a directed distance graph built from Ho Chi Minh City (HCMC) bus route geometry. It combines a reproducible data pipeline, multiple routing algorithms, concurrent query processing, independent correctness checks, and benchmark reports.

Python prepares the data and verifies results. The C++20 library implements Dijkstra, A*, Weighted HPA*, and bidirectional Weighted HPA*. Both command-line tools use the same graph, router, and algorithm implementations.

## 2. Dataset and scope

| Component | Count |
|---|---:|
| RouteId values in the snapshot | 150 |
| Route direction/variant records | 297 |
| Raw coordinate samples | 73,735 |
| Unique vertices | 38,148 |
| Directed edges | 42,170 |

The raw snapshot is stored in `data/raw/paths.jsonl`; the canonical graph is `data/processed/graph.json`. Vertex IDs follow the exact `(lat,lon)` sort order, without snapping nearby points together. Weights are Euclidean distances under the EPSG:3405 projection, in meters. Self-loops are removed, and duplicate edges in the same direction are merged using the minimum weight.

Vertices describe route geometry and are not necessarily bus stops. The graph has no route membership, waiting times, schedules, live traffic, or transfer costs. RouteId is an internal identifier, not a public route number. The collection date is unknown; this is a historical snapshot, and normalization does not update it to the current bus network.

## 3. Data pipeline

Python preprocessing validates the raw JSONL, builds JSON schema v1, and exports a text representation for C++. The text contains vertex IDs, lat/lon, projected x/y coordinates, and edges. Numeric values use 17 significant digits, and edge distances are taken directly from JSON. The export manifest records SHA-256 hashes, counts, provenance, and pyproj/PROJ versions; the collection date is null.

The entry point is `scripts/busmap_data.py`, with `build`, `export`, `queries`, and `check` commands. Default paths are independent of the working directory. Writers refuse to overwrite input/raw data and use temporary files followed by replacement for each output. JSON remains canonical so the C++ text representation can be checked independently.

Earlier Python implementations remain in `python/reference/`. Their known limitations include inconsistent A* return types between cache hits and misses, and ID-based partitioning, one-directional caching, and incorrect early termination in the hierarchical implementation. They are historical references, not correctness oracles for the C++ algorithms.

## 4. Routing algorithms and architecture

The `busmap` library contains the immutable CSR graph, loader, query/result types, router, algorithms, and worker pool. The loader validates headers, version, coordinate systems, units, dense IDs, sorted/unique coordinates and edges, endpoints, weights, and file length.

| Algorithm | Approach | Query resources |
|---|---|---|
| `dijkstra_baseline` | Dijkstra on the original graph | New distance, parent, and heap storage per query |
| `dijkstra` | Dijkstra on the original graph | Per-worker workspace and heap reuse; reset touched distances |
| `astar` | Heuristic-guided search on the original graph | Reusable search arrays and a local priority queue |
| `hpa` | Weighted A* over original edges and precomputed cluster shortcuts | Shared immutable index and private reusable workspace |
| `bihpa` | Bidirectional Weighted search over the same hierarchical graph | Shared base/reverse indexes and two private search states |

HPA partitions projected coordinates into clusters and retains all boundary portals. Dijkstra precomputes internal shortcut distances and complete paths; query-time search uses those shortcuts, then concatenates their stored paths. The current defaults are L=3500 m and w=1.05. Only Weighted search with w>1 and reusable workspaces is supported; historical exact/fresh modes have been removed.

BiHPA adds reverse adjacency and bidirectional search, using the same cluster configuration and shortcut paths. Each worker caches heuristic values only within the current query. Neither hierarchical algorithm caches completed query results.

`busmap-query` loads the graph once and processes queries from a file or stdin. With one worker, queries run sequentially; multiple workers use a fixed pool with bounded queues. Each worker owns its search state and shares the read-only graph and indexes. Results are emitted in input order. The graph must outlive the routers, indexes, and pool that reference it.

Input and output include a version, graph identity hash, query IDs, and counts. Diagnostics go to stderr and results to stdout. The CLI returns 0 for completed found/unreachable queries and 1 for invalid input, I/O errors, or invalid vertices. The format also reserves `not_implemented`, which the checker rejects; the implemented routing algorithms do not rely on Python fallbacks.

## 5. Independent verification

The Python oracle uses Dijkstra with a heap and stale-entry checks. Its tests include known small graphs and an independent Floyd–Warshall implementation, with zero-weight edges and unreachable pairs.

The HCMC benchmark suite contains 1,000 unique reachable pairs, with seed 162163. A pool of 3,000 pairs is sampled with at most 10 targets per source; sources come from a shuffled ordering of the entire ID range. The pool is divided by distance, then 334/333/333 queries are selected from the three groups. This synthetic sampling does not represent real user traffic.

The checker validates hashes, the query ID set, statuses, directed edges, endpoints, and total weights. `optimal` mode requires the oracle distance within tolerance. `any` mode accepts any valid path and reports its distance gap, while still rejecting invalid paths and incorrect reachability. Different equally optimal paths are accepted. For a positive-cost path with a zero optimum, `any` reports the extra distance in meters and a null percentage gap. The aliases `exact` and `approximate` remain available for the two checker modes.

Fixtures cover source=target, invalid IDs, one-way edges, isolated vertices, zero-weight edges/cycles, and tied paths. Fixture weights are manually chosen; coordinate-based heuristics are not automatically admissible on every fixture. C++ algorithm tests additionally compare all source/target pairs on 100 small directed graphs against independent Floyd–Warshall results. Pool and CLI tests exercise multiple producers, small queues, streaming, and output ordering.

## 6. Benchmarking and current results

`busmap-bench` runs the same implementations as the query CLI. It loads the graph once per process, warms up the executor, then measures repeated batches of the fixed workload. Service time includes resource initialization/reset, search, and full path reconstruction. Queue wait and end-to-end query latency are reported separately; batch wall time determines throughput. Graph loading, index construction, file writes, and checking are outside query timing.

The [README benchmark tables](README.md#latest-recorded-benchmark-results--2026-09-18) summarize the latest recorded results for one and four workers, including both Dijkstra variants. The Dijkstra comparison was recorded in a later session than the A*/HPA*/BiHPA* measurements; the tables identify those sources rather than treating them as one controlled experiment.

All recorded measured batches passed their applicable checkers. Dijkstra and A* matched optimal distances within tolerance on the evaluation suite. Weighted HPA achieved a p95 distance gap of 0.014112% and a maximum of 1.251586%; BiHPA achieved 0.065208% and 1.622314%, respectively. Within each algorithm, outputs were identical across worker counts and repeated runs.

Four workers improve total throughput by processing different queries concurrently. They do not parallelize an individual query. The recorded HPA/BiHPA results do not establish a consistent advantage for bidirectional search, and the Dijkstra variant results do not establish a stable whole-query speedup from workspace reuse. Process-to-process variation, background load, and scheduling limit conclusions from small timing differences.

HPA tuning uses a separate 1,000-query suite whose sources are disjoint from the evaluation suite. Configuration is locked before held-out measurement. Reports retain raw results, correctness and quality checks, preprocessing time, index/workspace memory, and peak RSS. Committed benchmark JSON summaries record the configurations and results of earlier experiments separately from current behavior.

## 7. Project resources

- [README](README.md): setup, usage, and current benchmark summaries.
- [Query suite](benchmarks/README.md): sampling, verification, and benchmark protocols.
- [C++ interfaces](cpp/include/busmap/): graph, routing, workspace, and worker-pool APIs.
- [Data manifest](data/processed/graph.manifest.json): snapshot provenance, hashes, and generator versions.
- [All-algorithm measurements](benchmarks/algorithms-20260918.json) and [Dijkstra measurements](benchmarks/dijkstra-variants-20260918.json): saved configurations, results, and limitations.
