# BusMap — C++ infrastructure handoff report

## 1. Project objective

BusMap currently focuses on CS163 Task 2: building a graph from HCMC bus route geometry and studying shortest-path algorithms. Python handles data processing and verification. The user implements the C++ core, including Dijkstra, A*, HPA*, heuristics, workspaces, and caches.

This version provides the environment for implementing algorithms: a canonical graph, CSR loader, CLI, test dataset, and checker. The three C++ algorithms still return `not_implemented`; no C++ shortest-path results or performance measurements are available yet.

## 2. Data

| Component | Count |
|---|---:|
| RouteId values in the snapshot | 150 |
| Route direction/variant records | 297 |
| Raw coordinate samples | 73,735 |
| Unique vertices | 38,148 |
| Directed edges | 42,170 |

Raw `paths.jsonl` and canonical `graph.json` remain byte-for-byte unchanged in this phase. IDs follow the exact `(lat,lon)` sort order; nearby points are not snapped together. Weights are Euclidean distances under the EPSG:3405 projection, in meters. Self-loops are removed; duplicate edges in the same direction are merged using the minimum weight.

Vertices describe route geometry and are not necessarily stops. The graph has no route membership, waiting times, schedules, traffic, or transfer costs. RouteId is an internal identifier, not a public route number. The collection date is unknown; this is a historical snapshot, and normalization does not turn it into the current bus network.

## 3. Pipeline and handoff files

Python preprocessing reads/validates the raw data, builds JSON schema v1, and exports text for C++. The text contains IDs, lat/lon, projected x/y coordinates, and edges. Numeric values use 17 significant digits; edge distances are taken directly from JSON. The manifest records SHA-256, counts, provenance, and pyproj/PROJ versions; the collection date is null.

The main commands in `scripts/busmap_data.py` are `build`, `export`, `queries`, and `check`. Defaults are independent of the working directory. Writers refuse to overwrite input/raw data and use a temporary file followed by replacement for each output. JSON remains canonical so that the text file can be verified against an independent representation.

The historical Python algorithms have moved to `python/reference/`, preserving their algorithm bodies. The old A* still has inconsistent return types between cache hits and misses; the old hierarchical implementation still has issues with ID-based partitioning, a one-directional cache, and incorrect early termination. They are not the oracle for C++.

## 4. C++ architecture

The `busmap` library contains Graph, the loader, query/result types, the router, and three stubs. Graph uses CSR with read-only getters. The loader checks headers/version/CRS/unit, dense IDs, sorted/unique coordinates, endpoints, sorted/unique edges, weights, and file length.

`busmap-query` loads the graph once, receives multiple queries from a file/stdin, and calls the router. Input and output include a version, graph hash, query IDs, and counts. The hash tag identifies the dataset; C++ does not hash the JSON file itself. Diagnostics go to stderr; results go to stdout.

Result statuses are found, unreachable, invalid_vertex, and not_implemented. The CLI returns exit code 2 for a stub, 1 for invalid input or an invalid vertex, and 0 when found/unreachable queries complete. There is no Python fallback.

CMake uses C++20 with no JSON library dependency. A*/HPA*/caching are not yet implemented. When HPA is added, its index must be owned by a solver/router that persists across queries; it must not be rebuilt for each pathfinding call.

## 5. Oracle, queries, and checker

The separate Python oracle uses Dijkstra with a heap and stale-entry checks. Tests compare it against small reference answers and independent Floyd–Warshall results on small graphs with zero-weight edges and unreachable pairs.

The HCMC query suite contains 1,000 unique reachable pairs, with seed 162163. The pool contains 3,000 pairs with at most 10 targets per source; sources are selected from a shuffled ordering of the entire ID range. The pool is divided by distance, then 334/333/333 queries are sampled, retaining group labels and oracle distances. This is synthetic sampling and does not represent real usage.

The checker validates hashes, the query ID set, statuses, directed paths, endpoints, and total weights. Optimal mode compares distances with the optimum within tolerance; any mode accepts every valid path and reports gaps while still rejecting invalid paths or incorrect reachability. Any mode permits positive-cost paths when the optimum is 0; it reports extra distance in meters and a null percentage gap. The old names exact/approximate remain aliases. Different equally optimal paths are accepted. NotImplemented always fails.

Fixtures add source=target, invalid IDs, one-way edges, isolated vertices, zero-weight edges, and tied paths. Fixture weights are chosen manually; a coordinate-based heuristic is not automatically valid on them.

## 6. Testing and next steps

Test groups cover the earlier normalization, byte preservation, reproducible export, round-tripping every node/edge, reproducible queries and oracle distances, checker failures, the CSR loader on real data, CLI file/stdin input, and malformed input.

Passing scaffold tests confirms the infrastructure, not the C++ algorithms. After implementing Dijkstra, update the stub assertions and run the checker on the fixture and complete query suite. Then implement A* and HPA*, measuring preprocessing, queries, and RAM separately. Concurrency, a newer bus network, and live traffic are outside this phase.

Read README for build/run commands and `docs/ARCHITECTURE.md` to start implementing. The wire format is in `docs/CPP_FORMAT.md`, JSON in `docs/DATA_FORMAT.md`, and sources/historical backups in `docs/SOURCES.md`. The original Task 2 report remains unchanged in docs; its historical benchmarks have not been rerun with the new architecture.

## 7. Timing benchmark addition

After the user implemented Dijkstra/A*, the busmap-bench target and scripts/benchmark.py were added. The stub descriptions above refer to the original handoff; the user may since have implemented the C++ code. The benchmark calls the current implementation through Router and uses the checker to determine correctness.

Each algorithm loads the graph once, warms up on one batch, and measures five batches by default on the fixed query suite. Graph loading, router setup, and individual query calls are measured separately; reports include mean/p50/p95 and totals per batch. Search and reconstruction are included; file I/O, checking, and destruction of returned results are outside the query timer. Raw samples are retained, and every run is checked. Optimal/any mode changes only the acceptance criterion, not the algorithm. Concurrency/caching have not yet been benchmarked; these results should not be generalized to every workload.

## HPA* implementation update — 2026-09-18

The stub descriptions and concurrency limitations above are historical handoff notes. C++ now includes Dijkstra, A*, Weighted HPA, and a worker pool. HPA retains one implementation: partition by coordinates in meters, retain all directed portals, precompute shortcut distances and paths with Dijkstra, search with Weighted A*, then concatenate the stored paths. The index is shared, and each worker always reuses its private workspace; there is no result cache yet. Correctness checks include the HCMC oracle and all pairs on 100 small graphs. The design and source-disjoint tuning/testing protocol are in docs/HPA_DESIGN.md; scripts/tune_hpa.py retains raw results and reports for each session.

Update, 2026-09-18: HPA retains only Weighted search (default L=3500 m, w=1.05)
and a reusable workspace; exact and fresh modes have been removed. Earlier
measurements remain in historical reports as comparison evidence.
