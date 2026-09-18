# Historical comparison of Weighted HPA* shortcut path storage

> This report records the 2026-09-18 experiment before code cleanup. Current source retains
> only Weighted HPA with full shortcut paths (`paths`); `refine`, `trees`, the storage-selection
> enum, CLI flag, and comparison script have been removed. Measurements below refer to the
> experimental binary whose hash is recorded in this report.

The three tested modes use the same graph, clusters, portals, shortcut weights, and Weighted A*
route search. Only construction of paths from shortcuts changes.

| Experimental label | Data stored in the index | Path construction during queries |
|---|---|---|
| `refine` | Shortcut distances | Run standard A* within the cluster |
| `paths` (retained) | Distances + full path for each shortcut | Read and concatenate NodeIds |
| `trees` | Distances + Dijkstra parent tree per source portal | Trace parents backward, reverse, and concatenate |

Each worker always reuses its workspace. The immutable index remains shared among workers.
HPA runs only Weighted search (`w>1`); these modes do not restore the removed exact or
fresh-workspace modes.

## Experimental implementation

The experimental version offered path-storage options; each index allocated data only for
the selected mode. The current API is `HpaIndex(graph, L)` and `HpaOptions{L, w}`,
always storing full paths.

All three modes run the same cluster-restricted Dijkstra from each portal. Dijkstra still
stops once all target portals are settled or the heap is empty. The two new modes store
`parent[v]` during relaxation. No additional search is run during index construction.

Sources are visited in NodeId order to write shortcuts directly into CSR. Each source's
shortcuts remain sorted by target. This new source order applies to all three modes,
avoiding an extra copy of the full path index during construction.

`paths` stores `path_offsets` in shortcut order and a contiguous `path_nodes` array.
Each segment omits the source and retains vertices from the next step through the target.
Total path memory is `O(shortcuts + total vertices in shortcut paths)`.
Shared segments can appear multiple times in the array.

`trees` assigns each vertex a cluster-local index. Each source portal with at least one
shortcut stores a parent row as long as that cluster's vertex count. Parents store original
NodeIds; the row is located by a source offset. Unreachable cells retain a sentinel.
It does not allocate N whole-graph entries per portal. Additional memory is
`O(N + Σ b'_C × n_C)`, where b'_C counts portals with at least one reachable shortcut.
These are dense parent rows within each cluster: they can exceed full-path storage when
the graph has many disconnected components or each portal reaches few others.
There is no compression to retain only necessary ancestors.

During queries, Weighted A* is identical. When a parent step is a shortcut:

- `refine` runs local A* as before.
- `paths` traverses the stored segment and appends it to the result path.
- `trees` traces parents into a reusable segment vector, then concatenates in forward order.

Both stored-path modes retain only NodeIds, without duplicating weights for every step.
During concatenation, the code reads real graph edges to sum costs and checks that the
segment total matches the shortcut weight within tolerance. Cost lookup scans outgoing
edges of the preceding vertex; this can be significant in high-degree graphs. Neither
new mode uses a heap or local A* workspace during queries.

Equal-cost shortest paths may differ between preprocessing Dijkstra and refinement A*.
Verification does not require identical NodeId sequences across all three modes, but
each path must be valid and have the same status and cost within tolerance.

## Protocol

Keep `L=3500 m, w=1.05` fixed and use the existing 1,000-query suite. Do not retune
cluster size or weight from these experimental results.

A Release binary is copied into the session. Each group runs all three modes in separate
processes, rotating their order. Groups are shuffled with seed 162164. Run 5 groups for
each worker count, 1 and 4, with queue capacity 64. Each process has 2 warm-up and
10 measured batches. Benchmarks do not run concurrently.

The query timer includes route search and full path construction. Index construction and
checking are outside it. Each batch retains all results until the end; process peak RSS
includes temporary build memory, allocator storage, and retained paths. Index/workspace
bytes measure retained capacity, not peak RSS.

Tables use medians of process-level measurements. Speed comparisons also calculate ratios
within each group, then take their median to reduce the effect of machine-load changes
over time. Ranges are retained in JSON; the machine is not isolated from background load.

After main measurements, run a separate diagnostic pass for each mode. Route-search counters
and shortcut counts on the route must match; `local_expanded` for `paths`/`trees` must be 0.
Instrumented timings are reported separately.

## Results for 2026-09-18

**Use `paths` for this workload.** With one worker, the two stored-path methods have similar
latencies (about 0.2% between medians), but `paths` uses an index about 39% smaller than
`trees`. Compared with rerunning A*, both reduce mean query time by about 11% using ratios
of medians. After this comparison, only `paths` remains in the API, CLI, benchmark, and tuning code.

| Worker | Path construction | Mean µs | p95 µs | Query/s | Build ms | Index MiB | Workspace/worker MiB | Peak RSS MiB |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | refine | 166.20 | 455.83 | 6015.7 | 12.021 | 0.688 | 1.617 | 8.53 |
| 1 | paths | 148.23 | 426.29 | 6745.0 | 14.030 | 3.040 | 0.843 | 15.05 |
| 1 | trees | 148.52 | 428.71 | 6731.9 | 14.312 | 5.004 | 0.844 | 20.53 |
| 4 | refine | 184.13 | 505.29 | 21467.2 | 12.316 | 0.688 | 1.617 | 14.22 |
| 4 | paths | 160.87 | 470.12 | 24579.8 | 14.198 | 3.040 | 0.843 | 15.66 |
| 4 | trees | 169.04 | 486.46 | 23401.3 | 13.791 | 5.004 | 0.844 | 21.50 |

Taking ratios within groups before median aggregation gives one-worker latency reductions
of **11.92% (`paths`)** and **10.12% (`trees`)**. A median of ratios need not equal a ratio
of medians. The small one-worker difference between the two methods should not be
interpreted as a definite speed advantage.

With four workers, `paths` reaches about **24,580 queries/s**, `trees` about **23,401 queries/s**,
and `refine` about **21,467 queries/s**. There is only one shared index; workspace nearly
halves because local A* arrays are removed.

Relative to `refine`, `paths` adds about 2.01 ms to index construction and saves about
17.97 µs/query based on the two one-worker medians, giving a preprocessing break-even
point of about **112 queries**. The corresponding `trees` estimate is about **130 queries**.
These estimates cover only additional build cost, excluding graph loading or machine changes.

### Why are parent trees larger?

The graph produces 324 clusters, 3,596 portals, and 14,055 shortcuts. `paths` stores
**588,573 NodeIds** in the required paths, plus offsets. `trees` retains **1,877 parent rows**
with **1,017,045 parent cells**, plus source offsets and a NodeId-to-cluster-index mapping.
Each dense parent row includes cells not needed for reconstruction. On this graph, that
storage exceeds duplicated vertices in full-path storage. This conclusion applies only
to the tested parent-tree representation; compressed trees could have different tradeoffs.

### Path construction cost and vertex visits

| Mode | Route search µs/query | Path construction µs/query | Main-phase visits | Local A* visits |
|---|---:|---:|---:|---:|
| refine | 143.78 | 22.96 | 1081.56 | 423.87 |
| paths | 144.17 | 3.23 | 1081.56 | 0.00 |
| trees | 145.70 | 4.66 | 1081.56 | 0.00 |

Both stored-path methods reduce valid heap pops from about **1,505 to 1,082 per query**.
Traversing and copying NodeIds during path construction still costs time, but is not
another search expanding vertices. This table comes from a separate instrumented pass
and does not replace the main latency measurements.

### Verification

Completed **33 processes, 303 batches, and 303,000 checker-verified results** in 144.4 seconds.
Actual paths on this suite have the same NodeId sequences in all three modes. Maximum
distance difference is only **2.91×10⁻¹⁰ m**, due to floating-point addition order.
Both stored-path modes produce identical output. The new `refine` output matches the
version before path-storage options byte for byte.

Quality is unchanged: p95 gap **0.014112%**, maximum gap **1.251586%**; the checker considers
901/1,000 queries optimal. The Weighted bound is checked against the oracle. Overlay
counters and shortcut counts on the route match for every query.

All 6/6 Release CTest suites and 3/3 C++ ASan/UBSan CTest suites pass. HPA tests use
100 random graphs, all pairs, multiple L/w values, and all three storage modes; they
check each stored shortcut against cluster-restricted Floyd–Warshall, edge direction,
zero weights, disconnected clusters, workspace reuse, shared indexes, and mismatched configurations.

Measured binary: `2859bf682ad46793283c442185d470deb7787545f056896fbffb69e840d6c137`.
Compiler: `AppleClang 14.0.3.14030022`, Release C++20; `macOS-26.5-arm64-arm-64bit-Mach-O`.

- [Raw report and tables by distance group](../artifacts/hpa-path-storage-20260918/report.md)
- [JSON, per-process measurements, commands, and hashes](../artifacts/hpa-path-storage-20260918/report.json)
- [Summary CSV](../artifacts/hpa-path-storage-20260918/summary.csv)
- [Raw timings, paths, and checker results](../artifacts/hpa-path-storage-20260918/runs/)

## Running the current version

```sh
./scripts/bench.sh hpa
.venv/bin/python scripts/tune_hpa.py \
  --output-dir artifacts/hpa-tuning-new --budget-minutes 60
```

C++ uses `HpaOptions{3500, 1.05}` and `HpaIndex(graph, 3500)`.
The current version does not accept `--hpa-path-storage`.

Binaries, raw results, and source/script snapshots from the three-way experiment remain in
`artifacts/hpa-path-storage-20260918/` for historical comparison; the comparison script is
no longer in the active `scripts/` directory.

After removing the old branches, 1,000 queries were rechecked with 1 and 4 workers:
both result files match the selected `paths` version byte for byte. Release, ASan/UBSan,
and Python test suites all pass.
[Post-cleanup verification details](../artifacts/hpa-paths-only-validation-20260918-152141/report.md).
