# Bidirectional HPA*

`bihpa` performs Weighted search in both directions on the same abstract graph as `hpa`. Both algorithms share cluster partitioning, Dijkstra preprocessing, and fully stored shortcut paths. Bidirectional search is implemented separately in `cpp/src/bihpa.cpp`, with its interface in `cpp/include/busmap/bihpa.hpp`.

Defaults remain `L = 3500 m`, `w = 1.05`. Only finite `w > 1` is supported, as in current HPA. L/w have not been tuned separately for BiHPA. Here, `bihpa` means bidirectional **hierarchical** pathfinding, not the historical BHPA algorithm.

## Reading the code

1. `BiHpaIndex::BiHpaIndex`: builds incoming-edge CSR for the original graph and shortcuts.
2. `bihpa()`: validates input, searches, and constructs the returned path.
3. `OverlaySearch::run()`: main loop, direction selection, and termination condition.
4. `OverlaySearch::expand()`: traverses edges and updates distances/the meeting point.
5. `reconstruct_path()`: joins the two branches and expands shortcuts from stored data.

## Index and workspace

`BiHpaIndex` retains a `shared_ptr<const HpaIndex>` and adds two incoming-edge CSR structures. For an original edge `u → v`, the reverse adjacency of `v` contains `(u, weight)`. Shortcuts follow the same rule. `Edge::target` in reverse adjacency denotes the **predecessor** vertex.

The reverse structures do not copy shortcut paths. During reconstruction, both directions read the stored `u → v` path from the base index. The index is immutable and shared by all workers. The graph is borrowed and must outlive both the index and the Routers/QueryPools.

Each worker owns a `BiHpaWorkspace` containing two `HpaSearchState` objects and a vector for the branch before the meeting point. Distance, parent, heap, and touched-vertex vectors are reused. Reset clears only distances at vertices touched in the previous query. Query results are not cached, and the entire workspace is not recreated per call.

The workspace also retains two `double` vectors, `h_to_target` and `h_from_source`, a `uint32_t` vector `cache_epoch`, and a `query_epoch` counter. The first time a vertex is used to compute a priority in a query, both heuristics are computed and the epoch is recorded. Both directions reuse this pair when g improves or the vertex is reached from the other side. A new query increments the epoch, avoiding a full clear of the heuristic vectors. On epoch wraparound, all stamps are cleared to 0 and counting restarts at 1; changing the vertex count reinitializes the stamps. The workspace is reset exactly once before initializing the two search roots. Invalid/self queries do not read the cache; the next search query still increments the epoch. Additional memory is N × (2 × sizeof(double) + sizeof(uint32_t)), private to each worker.

Router/QueryPool accept an additional optional final parameter, `shared_ptr<const BiHpaIndex>`, preserving existing calls. If no index is supplied, setup builds it. The pool builds the base and reverse indexes synchronously before creating workers, then passes the same shared pointers to every Router. If only a bidirectional index is supplied, its base index is used; if both are supplied, they must match.

## The graph searched in both directions

In both directions, source and target clusters are expanded in detail:

- In the first/last clusters: traverse original edges, including edges leaving/entering other clusters.
- In intermediate clusters: traverse internal shortcuts and original inter-cluster edges.
- Forward uses outgoing edges; backward uses incoming edges of **that same combined graph**.

Backward search is therefore the true transpose of forward search, including for one-way graphs. No reverse edges are added to the original graph. A source/target pair in the same cluster may still leave and re-enter it. Clusters need not be connected; inter-cluster edges may skip multiple cells.

## Priority and termination

Uses WBAE* with `lambda = 1`, from [Shperberg et al., *Bidirectional Bounded-Suboptimal Heuristic Search with Consistent Heuristics*, §4](https://arxiv.org/html/2511.10272v1). Written with priorities divided by two:

```text
hF(v) = alpha * Euclidean(v, target)
hB(v) = alpha * Euclidean(v, source)

keyF(v) = gF(v) + (w * hF(v) - hB(v)) / 2
keyB(v) = gB(v) + (w * hB(v) - hF(v)) / 2

mu = minimum cost of a complete path found so far
Stop when mu is finite and mu <= minOpenKeyF + minOpenKeyB.
```

`alpha` comes from the existing HpaIndex. Each edge costs at least `alpha` times the geometric distance between its endpoints. The triangle inequality makes the heuristic consistent in both forward and reverse directions; shortcuts represent valid paths and satisfy this condition as well. Weighted priorities may be inconsistent, so the implementation allows expanded vertices to reopen when a smaller g is found.

Relaxing a vertex `v` with finite distance in the other direction yields a candidate `gF(v) + gB(v)` for updating `mu` and the meeting point. This only updates the best known path; it is **not the termination condition**. Before checking the bound, remove all stale entries at the tops of both heaps.

The WBAE* bound guarantees a result no greater than `w × optimum` for consistent heuristics under exact arithmetic. The implementation uses `double` without adding tolerance to relax the termination condition; verification allows a small numerical error. Increasing w does not guarantee a speedup for every query or a p95 error ≤ 1%.

Choose the direction with the smaller key; alternate on ties. Within each heap, break ties by g, then NodeId. This choice is reproducible. A query may expand mostly one side; the two sides need not perform equal work.

```text
reset both workspaces
push source into forward and target into backward
mu = infinity, meeting = none

loop:
    discard stale entries at the tops of both heaps
    if either heap is empty: stop
    if meeting exists and mu <= minKeyF + minKeyB: stop
    choose a direction and pop its minimum entry
    for each applicable edge in the combined graph:
        if g improves:
            record g, parent, edge type, and weight
            push a new entry, even for an expanded vertex
            update mu and meeting if the other side has reached this vertex

if no meeting exists: unreachable
otherwise: join source → meeting → target
```

If a heap becomes empty, that direction has exhausted its reachable region. If a source→target path exists, it must already have reached the other side's root. Without a meeting point, the result is unreachable.

## Reconstructing in the correct direction

A forward parent is the preceding vertex on source→meeting. A backward parent is the **next vertex toward the target in the original graph**.

- Trace forward parents backward, then reverse the branch into source→meeting.
- Follow backward parents directly from meeting to target.
- Append ordinary edges directly to the result.
- For shortcut `p → q`, call `append_shortcut_path(p, q, path)` on either branch without reversing the stored path.
- Recompute total cost from real edges; shortcut expansion returns the total cost of the path it appended.

Joining segments does not duplicate the connecting vertex. A walk may visit the same cluster repeatedly. Parents update only when g strictly decreases, so zero-weight cycles do not create parent loops.

## Memory, time, and diagnostics

Let N be the original vertex count, E the original edge count, and S the shortcut count. Building reverse CSR takes O(N + E + S) additional time and memory beyond HPA construction. The two workspaces contain O(N) arrays and heaps that grow with unpopped relaxations; Weighted search can reopen vertices, so each vertex is not assumed to expand only once. Result construction is proportional to the returned path's edge count, plus shortcut lookup.

Benchmark metadata:

- `hpa.index_bytes`, `hpa.index_build_ms`: base + reverse totals.
- `base_index_bytes`, `base_index_build_ms`: base HPA only.
- `reverse_index_bytes`, `reverse_build_ms`: reverse CSR only.
- `path_storage_bytes`: shortcut paths counted once.
- `max_hpa_workspace_bytes_per_worker`: largest vector storage observed in one worker.
- `peak_rss_bytes`: peak memory for the entire process, including graph, index, workspaces, and retained results; not index size.

Bytes are calculated from index/workspace vector capacities, excluding allocator overhead and object headers. Diagnostics run in a separate pass: `overlay_expanded = forward_expanded + backward_expanded` counts **expansion events**, possibly counting a vertex multiple times or in both directions. `peak_heap` is the combined entry count of both heaps, including stale entries not yet popped. `meeting_updates` counts improvements to the complete path. Expanding shortcuts does not repeat internal pathfinding.

`heuristic_requests` counts individual h values, increasing by 2 per priority; a cache miss increases `heuristic_evaluations` by 2. Cache hits do not call the heuristic again. Metadata `hpa.heuristic_cache = per_query`, `heuristic_cache_invalidation = epoch` distinguishes the cached version from historical measurements. Workspace bytes include both h vectors and the epoch vector. See the [before/after heuristic cache measurements](HEURISTIC_CACHE_RESULTS.md).

## Running

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j 4
./build/busmap-query --graph data/processed/graph.txt \
  --queries benchmarks/queries.txt --algorithm bihpa \
  --hpa-cluster-size 3500 --hpa-weight 1.05

./scripts/bench.sh bihpa --threads 4 --repetitions 10 --warmup 2

.venv/bin/python scripts/compare_bihpa.py \
  --output-dir artifacts/bihpa-comparison-new \
  --processes 6 --repetitions 10 --warmup 2 --threads 1 4
```

`compare_bihpa.py` fixes the configuration and runs balanced AB/BA ordering with an even process count, without concurrent benchmarks. It retains binary snapshots, the seed/schedule, data hashes, timing CSVs, results from each repetition, and checker output. Reported values are medians across processes; quality is computed once per query. `scripts/benchmark.py` defaults to the `any` checker plus a w-bound check for HPA/BiHPA; `--mode optimal` still changes only the checker, not the algorithm.

## Verification

`tests/cpp/test_bihpa.cpp` compares against independent Floyd–Warshall on 100 directed graphs, every source/target pair, 4 cluster sizes, and 3 weights. Fixtures check premature termination at the first meeting, reopening vertices, correctly directed shortcut expansion on both branches, leaving/re-entering the same cluster, disconnected graphs, zero cycles, singletons, negative/boundary coordinates, invalid IDs, and shared indexes. Pool tests include multiple producers and queue capacity 1. The CLI is compared with the Python oracle on 1,000 HCMC queries using both 1 and 4 workers. C++ tests also run with ASan/UBSan in a separate build.
