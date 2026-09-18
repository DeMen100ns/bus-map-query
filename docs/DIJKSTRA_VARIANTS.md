# Two Dijkstra variants

| Component | `dijkstra_baseline` | `dijkstra` |
|---|---|---|
| Implementation | `cpp/src/Dijkstra_baseline.cpp` | `cpp/src/dijkstra.cpp` |
| Distance, parent | New local vector per query | Vector in the worker's private workspace |
| Reset distance | Initialize all N vertices | Initialize on resize; otherwise reset only touched vertices |
| Parent | Initialize all N vertices | Overwrite on discovery; no full-array reset |
| Heap | New local priority queue | Vector heap retaining capacity across queries |
| Graph | Read-only reference | Read-only reference |
| Returned path | Result-owned vector | Result-owned vector |
| Checker | `optimal` | `optimal` |

The baseline does not take a workspace or use static/thread-local storage to retain resources.
Every valid query creates search containers inside the function, including self-queries.
Invalid query IDs are rejected before search resources are allocated.

The optimized variant records the source and each discovered vertex in `touched` exactly once.
The next query resets their distances and clears the heap while retaining vector capacities.
Parents are overwritten on discovery; only the source parent is reset to a sentinel.
Clearing the heap is necessary when the preceding search stopped early at the target. A graph
size change reinitializes arrays; a new graph with the same vertex count is also reset correctly.
Both variants break ties by `(distance, NodeId)`, discard stale heap entries before checking
the target, and update only when a smaller distance is found.

Reset cost in the optimized variant is O(k), where k is the number of vertices discovered
by the previous query; initial allocation and resizing remain O(N). When k is close to N,
the reset benefit may be small, and recording touched vertices also has a cost. The heap
retains its grown capacity, reducing allocations while holding memory between queries.
Both variants preserve Dijkstra's complexity and optimality for nonnegative weights.

In QueryPool, each worker retains its own Router; only `dijkstra` uses the Router's workspace.
The baseline creates resources inside each call even though the Router persists.
Query results are not cached. Benchmark metadata distinguishes the two policies;
query time includes initialization/reset, search, and path construction.

## Running

```sh
./scripts/bench.sh dijkstra_baseline --threads 4 --queue-capacity 64
./scripts/bench.sh dijkstra --threads 4 --queue-capacity 64
```

Or, after a Release build, run both sequentially with the same binary:

```sh
.venv/bin/python scripts/benchmark.py \
  --algorithm dijkstra_baseline dijkstra --threads 4 \
  --queue-capacity 64 --warmup 2 --repetitions 10
```

Replace `--threads 4` with `1` for a one-worker comparison. The CLI, benchmark executable,
and scripts all accept `--algorithm dijkstra_baseline` / `--algorithm dijkstra`.
Dijkstra benchmarks from before this change used distance/parent capacity reuse,
but reassigned the full arrays and created a new heap; they are not the new baseline.

## Verification

- All 8/8 Release CTest suites pass: Dijkstra, HPA, BiHPA, loader, pool,
  CLI, concurrency, and benchmark.
- All 5/5 C++ suites pass in a separate ASan/UBSan build.
- Dijkstra is checked for all pairs against Floyd–Warshall on 100 directed graphs
  generated with seed 162164, plus fixtures for one-way edges, zero-weight cycles,
  unreachable pairs, self-queries, invalid IDs, and leftover heap entries after target termination.
- Workspaces are reused across graphs of the same or different sizes and interleaved A* calls;
  earlier results are retained to verify that later queries do not change previous paths.
- The pool is tested with multiple producers and queue capacity 1. Both variants return
  identical results with one and four workers on 1,000 HCMC queries.

## Profiling reset

On the 1,000-query HCMC suite, a query discovers an average of 17,863.5 vertices
(46.8% of 38,148 vertices). These are **discovered**, not expanded, vertices.
Separate profiling times reset independently from search/path construction, using eight
alternating batches with/without parent reset after two warm-up batches:

| Reset by touched vertices | Median reset time/query |
|---|---:|
| Distance and parent | 12.724 µs |
| Distance only | 6.309 µs |

All paths in the profiling pass match the baseline. The final version resets only distance:
parents do not need resetting because they are always written before reconstruction.
Reset accounts for about 1% of query time in this measurement; reducing reset time does
not imply a proportional whole-query speedup. This instrumentation is used only in a
separate diagnostic pass and is disabled in the final benchmark.

## Final-version benchmark — 2026-09-18

Same Release binary with `-O3 -DNDEBUG -flto=thin`, AppleClang 14.0.3.14030022,
macOS arm64. Fixed suite of 1,000 HCMC queries, queue capacity 64. Each configuration
runs four independent processes, each with two warm-up and five measured batches.
Processes run sequentially, alternating variant and worker-count order by round.
Each table metric is a median across processes; no outliers are removed.

| Variant | Worker | Mean service (µs) | p95 service (µs) | Query/s | Peak RSS (MiB) | Range of process means (µs) |
|---|---:|---:|---:|---:|---:|---:|
| dijkstra_baseline | 1 | 1720.701 | 3523.646 | 612.7 | 7.06 | 942.2–2563.8 |
| dijkstra | 1 | 1327.377 | 2550.229 | 755.5 | 6.76 | 1186.0–3452.3 |
| dijkstra_baseline | 4 | 1874.228 | 3719.333 | 2125.9 | 9.52 | 1118.0–2009.5 |
| dijkstra | 4 | 2117.961 | 4393.750 | 1883.7 | 8.75 | 1893.1–2284.9 |

Service time includes allocation/reset, search, and full path construction, excluding
queue wait. Throughput uses wall time for the entire batch. Peak RSS includes the graph,
workers, and retained results; it is not workspace size.

All 80 measured batches pass the `optimal` checker; result files are byte-identical
between variants, processes, one/four workers, and the pre-change Dijkstra version.
Tiny differences from the Python oracle are only floating-point rounding.

The initial trial reset parents too and ran four processes/configuration with two warm-ups
and ten measured batches. Baseline/reused mean service times were 1069.1/1247.0 µs with
one worker and 2068.7/2353.8 µs with four workers. The initial trial is retained unchanged
under `previous_trial` in the JSON, with raw data in `comparison/`.

Process results vary substantially, so median differences between initial and final trials
cannot be attributed entirely to removing parent reset. Separate reset profiling demonstrates
less reset work; whole-query benchmarks establish actual performance.

In the final trial, median mean service time for the workspace variant is 22.9% below the
baseline with one worker, but 13.0% above it with four workers. The initial one-worker trial
showed the opposite result. There is therefore **no evidence of a stable whole-query speedup**
under this workload and measurement setup. Reducing allocation/reset does not automatically
improve speed: recording `touched`, indirect reset accesses, and search costs also matter.
These measurements do not separate those factors from machine variability.

Reproduction tool (output-dir must not already exist):

```sh
.venv/bin/python scripts/compare_dijkstra.py \
  --output-dir artifacts/dijkstra-comparison-new \
  --threads 1 4 --processes 4 --warmup 2 --repetitions 5
```

The [summary JSON](../benchmarks/dijkstra-variants-20260918.json) contains binary/data/query/answer
hashes, per-process measurements, short/medium/long groups, profiling results, and the trial
before parent reset was removed. Raw CSVs, individual result files, checker output, binaries,
and test logs are in `artifacts/dijkstra-variants-20260918-164323`.
