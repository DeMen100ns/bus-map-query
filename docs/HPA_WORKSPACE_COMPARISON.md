# HPA*: reusing a workspace versus creating one per query

> Historical report: current source has removed exact and fresh modes, retaining only Weighted
> search with reusable workspaces. Earlier measurements and binaries in artifacts are unchanged.

Both experimental modes use the same HPA* algorithm, graph, and Dijkstra index. Only the
working-memory lifecycle changes. The default remains `reuse`.

| Component | `reuse` | `fresh` |
|---|---|---|
| Graph and index | Shared, index built once | Same as `reuse` |
| `distance`, `parent`, `parent_weight`, shortcut flag | Retain capacity between queries | New vectors, allocated/initialized when needed by a query |
| Heap and `touched` list | Retain capacity | Create/destroy per query |
| Vector `route`, `segment` | Retain capacity | Create/destroy per query |
| A* shortcut expansion | Workspace separate from route search | Same as `reuse`; reused across shortcuts within the **same** query |
| Returned path | Result-owned vector | Same as `reuse` |
| Invalid/self-query | Return early | Return early without forcing unnecessary array allocations |

This comparison measures the combined effect of allocation, initialization, and deallocation,
while removing capacity retention across queries. The difference cannot be described as
`malloc` time alone. `reuse` resets distances through the touched list; `fresh` initializes
arrays according to vertex count on first use. In both modes, local workspace is allocated
only if a shortcut needs expansion.

## Measurement methodology

- Keep the existing 1,000-query suite and independent oracle unchanged.
- Retain selected configurations: exact `L=1900, w=1`; Weighted `L=3500, w=1.05`.
- Copy the same Release build into the experiment directory; its hash is in the JSON.
- Each configuration runs 5 independent processes, each with 2 warm-up and 10 measured
  batches. Measure 1 and 4 workers separately, with queue capacity 64.
- Run `reuse` and `fresh` consecutively in pairs, alternating which runs first by pair number.
  Shuffle pair order with seed `162164`. Benchmarks do not run concurrently and compete
  for CPU; the machine is not isolated from OS background load.
- The service timer covers all of `Router::query()`: initialization, search, shortcut expansion,
  path construction, and workspace destruction. Index building, checking, and writing are outside it.
- Both modes retain result paths until the end of the batch; result destruction is outside
  the service timer. Warm-up warms the allocator; pages are not forced back to the OS.
- Mean/p50/p95 and throughput in the tables are medians of 5 process-level measurements.
  Each process percentile uses nearest-rank over 10,000 calls.
- Compute `fresh/reuse` within each pair, then take the median; also report the minimum–maximum
  range so variation between processes is visible.

`max_hpa_workspace_bytes_per_worker` is the largest total vector capacity observed in one
worker, including temporary `fresh` workspace. `retained` is capacity still owned by the
workspace between queries, which is 0 for `fresh`. It excludes allocator metadata, stack
objects, the index, and result paths. `Peak RSS` covers the entire process; the allocator
may retain pages after vectors are freed. Thus `retained = 0` does not imply lower RSS.

## Results for 2026-09-18

On this workload, **creating a fresh workspace is slower**. With one worker, the median
of within-pair ratios is **+8.53% for exact** and **+8.44% for Weighted**. Keep `reuse`
as the default. Values below come from this measurement using the same binary;
previous tuning measurements are not used to calculate differences.

| Algorithm | Worker | Workspace | Mean (µs) | p50 (µs) | p95 (µs) | Query/s | Peak RSS (MiB) |
|---|---:|---|---:|---:|---:|---:|---:|
| exact | 1 | reuse | 173.56 | 117.33 | 545.83 | 5761.0 | 9.28 |
| exact | 1 | fresh | 187.84 | 132.00 | 559.88 | 5323.0 | 10.05 |
| exact | 4 | reuse | 186.23 | 124.92 | 586.88 | 21216.1 | 14.42 |
| exact | 4 | fresh | 202.70 | 141.62 | 605.21 | 19494.1 | 18.56 |
| weighted | 1 | reuse | 162.05 | 126.25 | 441.38 | 6170.1 | 9.14 |
| weighted | 1 | fresh | 175.75 | 140.25 | 453.54 | 5689.1 | 9.67 |
| weighted | 4 | reuse | 179.60 | 139.38 | 488.33 | 22010.1 | 14.52 |
| weighted | 4 | fresh | 190.70 | 152.17 | 492.08 | 20724.2 | 18.31 |

| Algorithm | Worker | Fresh slowdown: paired median | Range across 5 pairs |
|---|---:|---:|---:|
| exact | 1 | 8.53% | 5.37–15.77% |
| exact | 4 | 8.98% | 8.22–9.53% |
| weighted | 1 | 8.44% | 3.23–9.33% |
| weighted | 4 | 9.81% | 5.95–27.35% |

The median of paired ratios need not equal the ratio of two medians. One Weighted
four-worker pair differs by 27.35%; it is retained, without removing samples to improve
the figures. Four-worker results vary more, so throughput gains should not be treated
as constants applicable to every machine.

By distance group, one worker:

| Algorithm | Group | Reuse mean (µs) | Fresh mean (µs) | Increase from the two medians |
|---|---|---:|---:|---:|
| exact | short | 65.90 | 79.46 | 20.58% |
| exact | medium | 167.93 | 181.81 | 8.26% |
| exact | long | 287.09 | 302.15 | 5.24% |
| weighted | short | 79.06 | 91.62 | 15.89% |
| weighted | medium | 170.82 | 184.87 | 8.23% |
| weighted | long | 236.53 | 250.66 | 5.98% |

Short exact queries increase by about **20.6%**, compared with **5.2%** for long queries.
The absolute differences between the medians are about 13.6 and 15.1 µs, respectively:
workspace management accounts for a larger share when pathfinding is fast.

The `reuse` workspace retains up to about **1.59 MiB/worker** for exact and **1.62 MiB/worker**
for Weighted. `fresh` retains 0 bytes of vector capacity between queries but still needs
nearly the same amount while running. Measured peak RSS for `fresh` is higher: exact with
one worker uses **10.05 versus 9.28 MiB**, and four workers use **18.56 versus 14.42 MiB**.
This does not measure allocator fragmentation; there is insufficient evidence to attribute
the entire RSS difference to one specific cause.

## Verification and raw data

Completed **40 processes, 400 measured batches, and 400,000 checked results** in 186.2 seconds.
All paths and costs are identical across policies, repetitions, processes, and worker counts
for the same L/w. Exact passes the `optimal` checker; Weighted passes `any`. Index structure
and bytes are identical between policies. All 6/6 Release CTest suites and 3/3 C++ ASan/UBSan
suites pass. Tests compare all pairs on 100 random graphs with multiple L/w values and also
compare `fresh` against `reuse`; they check shared indexes, small queues, CLI results,
and benchmark metadata.

Compiler: `AppleClang 14.0.3.14030022`, Release C++20; `macOS-26.5-arm64-arm-64bit-Mach-O`.
Binary SHA-256: `f28b2991f4858fe5da227bd86cd6e2a63cd4c6b551f51156d03789e052015f85`.

- [Raw report](../artifacts/hpa-workspace-20260918/report.md)
- [JSON: individual processes, commands, schedule, and hashes](../artifacts/hpa-workspace-20260918/report.json)
- [Summary CSV](../artifacts/hpa-workspace-20260918/summary.csv)
- [Per-run timings, paths, and checker output](../artifacts/hpa-workspace-20260918/runs/)
- [Historical script source](../artifacts/hpa-workspace-20260918/source/compare_hpa_workspace.py)

The current version runs with `./scripts/bench.sh hpa`; the `--hpa-workspace` option
no longer exists. See the [current design](HPA_DESIGN.md) for the API and CLI.
