# Per-query heuristic caching — 2026-09-18

Vector-based heuristic caching has been added to HPA and BiHPA. The cache avoids recomputing
about 21.1% / 19.6% of heuristic values on the tuning suite. Returned paths and search-step
counts match the previous version. Median one-worker time decreases 2.9% / 5.8% in this
measurement, but some processes show substantial noise; a stable benefit across all workloads
has not been established.

## Storage

- HPA: `HpaWorkspace::heuristic`, one double vector indexed by NodeId. Compute h while
  `distance[v]` is still infinity, before assigning finite g; initialize the source separately.
  On g improvement or reopening, read h from the vector. Resetting distances at touched nodes
  also invalidates old cache values.
- BiHPA: two double vectors, `h_to_target` and `h_from_source`, and one uint32 vector,
  `cache_epoch`, shared by both directions within each worker's private workspace.
  Increment `query_epoch` at the start of each search. The first encounter with a vertex
  in the current epoch computes both h values; subsequent encounters reuse them. On uint32
  wraparound, clear stamps and restart at epoch 1. Changing vertex count also reinitializes stamps.
- Vectors retain capacity across queries, but values are valid only within their query.
  The cache contains h before multiplication by w. Invalid/self queries do not use the cache;
  the next search query still resets correctly. Changing the graph/index or endpoints does
  not retain valid heuristic values from the preceding query.
- Index, clusters, shortcut paths, priorities, termination, and tie-breaking are unchanged.
  The preprocessing Dijkstra workspace gets no cache vector. No cache on/off mode is added
  to the main algorithms; the previous version exists only in the benchmark snapshot.

## Measurement

Same AppleClang 14.0.3.14030022 compiler, Release, L=3500 m, w=1.05; no LTO or compiler-flag
changes. Two binary snapshots are compared using `scripts/compare_hpa_binaries.py`.
Each algorithm/worker/version combination has 4 processes, each with 2 warm-ups and 8 measured
batches. The 32 measurement processes run sequentially, with balanced before/after order
shuffled using seed 162164. One worker uses Router directly; four workers use a pool with
queue capacity 64. Service includes constructing the full returned path.

Uses the existing 1,000 tuning queries with seed 162164; sources are disjoint from the evaluation
suite. L/w are unchanged after measurement. A separate confirmation follows on 1,000 evaluation
queries with seed 162163. Checking every batch, file writes, and instrumentation are outside
main timing. Warm-up retains capacity/allocator state, not valid heuristic values from previous queries.

## Timing on the tuning suite

Medians across processes; each process uses nearest-rank p95 over 8,000 calls:

| Algorithm | Worker | Mean before (µs) | Mean after (µs) | p95 before (µs) | p95 after (µs) | Throughput before/after (q/s) |
|---|---:|---:|---:|---:|---:|---:|
| HPA | 1 | 150.539 | 146.229 | 441.062 | 420.271 | 6654.7 / 6832.5 |
| BiHPA | 1 | 154.590 | 145.615 | 498.333 | 481.229 | 6468.5 / 6866.4 |
| HPA | 4 | 164.564 | 159.709 | 482.188 | 466.666 | 23995.2 / 24735.5 |
| BiHPA | 4 | 178.456 | 180.162 | 566.000 | 564.354 | 22006.2 / 21815.7 |

One worker: median mean HPA time decreases 2.86%, BiHPA 5.81%. Four workers: HPA throughput
increases about 3.1%, while BiHPA decreases about 0.9%; BiHPA shows no benefit in this measurement.

**Limitations:** cores/frequency are not pinned and background load is not isolated. Before
caching, one HPA one-worker process takes 256.44 µs versus 141.63 µs for the fastest process;
after caching, one HPA four-worker process takes 414.12 µs versus 153.44 µs for the fastest.
All results are retained without outlier removal. The direction of change differs across
before/after pairs; the percentages above are observed median changes, not guaranteed speedups
or statistical conclusions. Raw data retains minimum/maximum means and every process.

## Evaluation counts and memory

Separate instrumentation on 1,000 tuning queries. Requests/evaluations count individual h values,
not vertices; BiHPA needs two h values per priority. An evaluation is a call to
`HpaIndex::heuristic`; alpha > 0 on this graph, so every call computes hypot.

| Average per query | HPA | BiHPA |
|---|---:|---:|
| Heuristic requests | 1466.631 | 2725.664 |
| Heuristic evaluations | 1157.457 | 2190.270 |
| Evaluations avoided | 309.174 | 535.394 |
| Cache hit rate | 21.08% | 19.64% |
| Vertex expansions, before = after | 1072.502 | 959.731 |

| Memory per worker | HPA | BiHPA |
|---|---:|---:|
| Additional cache, bytes | 305,184 | 762,960 |
| Additional cache, KiB | 298.031 | 745.078 |
| Workspace before, tuning suite | 859,476 | 1,717,928 |
| Workspace after, tuning suite | 1,164,660 | 2,480,888 |

Cache size scales with N: HPA N × 8 bytes; BiHPA N × 20 bytes, excluding object headers.
Workspace counts capacity, including heaps and temporary paths, so it can differ by workload:
on the evaluation suite, HPA after caching uses 1,189,236 bytes; BiHPA uses 2,480,888 bytes.
Index size remains 3,187,800 bytes for HPA and 4,697,784 bytes for BiHPA; the cache is not
counted in the shared index memory. Per-process peak RSS is in the JSON and must not be
used to infer cache size.

## Correctness

- All 7 CTest suites pass, including all-pairs tests on 100 random graphs for each algorithm,
  a Floyd–Warshall oracle, CLI, multiple workers/producers, and benchmark tests.
- All 4 C++ suites pass ASan/UBSan, including forcing the epoch to UINT32_MAX, contaminating
  old cache values, and checking the query after wraparound. Tests change graphs with the
  same/different sizes, source, target, and w; interleave invalid/self/unreachable queries;
  and compare reused versus fresh workspaces. A reopening fixture confirms heuristic reuse.
- Every tuning batch is valid and satisfies the w bound; results are byte-identical before/after,
  across repetitions and workers. Expansion, edge, stale-entry, heap-peak, shortcut, and meeting
  counters match for each query in the diagnostic pass.
- Independent evaluation suite: 1 batch before, 2 after, plus post-cache instrumentation.
  Both algorithms retain output hashes and quality. These few latency samples are not used
  to claim performance improvements.

| Evaluation suite | HPA | BiHPA |
|---|---:|---:|
| Valid paths | 1,000/1,000 | 1,000/1,000 |
| Matching optimum per checker | 901/1,000 | 886/1,000 |
| p95 gap | 0.014112% | 0.065208% |
| Max gap | 1.251586% | 1.622314% |

## Reproduction

The [summary JSON](../benchmarks/heuristic-cache-comparison.json) retains the run schedule,
hashes, medians/ranges, diagnostics, evaluation results, and before/after source hashes.
The [raw session](../artifacts/heuristic-cache-20260918-160127/tune/report.json) contains timing CSVs,
per-batch results/checker output, binary snapshots, and logs; test logs are in
`artifacts/heuristic-cache-20260918-160127/validation/`. Artifacts are ignored by Git.

```sh
.venv/bin/python scripts/compare_hpa_binaries.py \
  --before artifacts/heuristic-cache-20260918-160127/busmap-bench-before \
  --after build/busmap-bench \
  --queries artifacts/hpa-tuning-20260918/tune/queries.txt \
  --answers artifacts/hpa-tuning-20260918/tune/answers.json \
  --output-dir artifacts/heuristic-cache-new \
  --processes 4 --repetitions 8 --warmup 2 --threads 1 4
```

Binary before: `55c0c8abf8b10ddc112f3f5794959df2edb973898888b458cae5d0edf76e32d9`.
Binary after: `f799a885b9661bfe2d9e1850cc10ba6b170e3baaf0d67103a662b151e4520e02`.

New metadata: `hpa.heuristic_cache = per_query`,
`hpa.heuristic_cache_invalidation = first_touch|epoch`. Diagnostic CSVs add
`heuristic_requests` and `heuristic_evaluations`. Earlier HPA/BiHPA reports contain
historical measurements without heuristic caching.
