# C++ text contract — version 1

UTF-8/ASCII, one record per line, with whitespace-separated tokens. Files end with a newline and contain no blank lines or comments. Integers are decimal without a `+` sign. Floating-point numbers use a decimal point and must be finite; the exporter uses 17 significant digits. Header order is fixed.

## Graph

```text
BUSMAP_GRAPH 1
GRAPH_SHA256 <64 lowercase hexadecimal characters>
COORDINATE_CRS EPSG:4326
DISTANCE_CRS EPSG:3405
WEIGHT_UNIT meter
DIRECTED 1
NODES <N>
<id> <lat> <lon> <x_m> <y_m>
... exactly N lines
EDGES <M>
<source> <target> <distance_m>
... exactly M lines
```

- `0 < N <= UINT32_MAX`; IDs are consecutive from 0..N-1 and match line order.
- `(lat, lon)` pairs are unique and sorted ascending; lat is in [-90,90], lon in [-180,180]. x/y must be finite.
- Edges are sorted ascending by `(source,target)`, with no duplicates or self-loops; endpoints exist and weights are nonnegative. Zero-weight edges between distinct vertices are valid.
- Reverse edges are not added automatically. M may be 0; isolated vertices are retained.
- SHA-256 is the hash of the canonical JSON bytes. C++ checks tag syntax and compares it with queries; it does not read/hash JSON or verify a checksum of the entire text file. The Python manifest contains the text checksum for independent integrity checks.
- JSON v1 remains canonical. Rebuilding JSON with different bytes changes the identity tag even if only metadata changes; the corresponding text, queries, and answers must be regenerated.

## Query

```text
BUSMAP_QUERIES 1
GRAPH_SHA256 <JSON hash>
QUERIES <Q>
<query_id> <source> <target>
... exactly Q lines
```

Query IDs are uint64 and unique within a batch. Source/target are int64 so invalid IDs can be represented; the router returns `invalid_vertex` for values outside 0..N-1. Q may be 0. The CLI defaults to dijkstra and accepts a file or stdin. The graph is loaded only once.

## Result

```text
BUSMAP_RESULTS 1
GRAPH_SHA256 <JSON hash>
RESULTS <Q>
<query_id> <status> <distance_m or -> <K> <K node IDs>
```

Status is exactly one of `found`, `unreachable`, `invalid_vertex`, or `not_implemented`. Node IDs are uint32.

- Found: finite, nonnegative distance, K>0, and a path starting/ending at the query endpoints.
- Source equal to target: `found 0 1 <source>`.
- Other statuses: `- 0`, with no additional nodes.
- The query ID set matches the input; the checker permits reordered results.
- `not_implemented` always fails the checker and cannot stand in for unreachable.

## Oracle and checker

`answers.json`: `schema_version=1`, `graph_sha256`, `queries_sha256`, and an `answers` array containing `query_id`, `status`, and `distance_m`; benchmark queries also have `group`. Non-found results use JSON null. Infinity/NaN are not used.

The checker requires matching graph identities across all files and a query-byte SHA matching the answers. It validates paths against directed edges in JSON, sums weights using `math.fsum`, and compares the sum with the returned distance. Incorrect reachability, missing/duplicate queries, invalid paths, and unimplemented statuses all fail.

Tolerance: `abs(a-b) <= max(1e-6, 1e-9 * max(abs(a), abs(b)))`.

- `optimal` (default): the distance matches the optimum within tolerance.
- `any`: any valid path is accepted, without a gap threshold relative to the optimum. Reachability, edge direction, endpoints, total weight, and the source=target contract are still checked.
- Both modes report gaps for each valid found path, mean/max percentage gaps, maximum extra distance in meters, the number of optimal paths, and the number of valid found paths. A path shorter than the oracle beyond tolerance produces an error so that the data/oracle can be investigated.
- When the optimum is 0 and another path has positive cost, any mode accepts it. The percentage gap is null; extra distance in meters is still available. Mean/max percentages use only defined values; undefined_relative_gap_count records how many cases were excluded from that calculation.
- `exact` aliases `optimal`; `approximate` aliases `any`. The approximate alias also follows the new zero-optimum rule.
- Selecting a mode does not modify the algorithm; it specifies output verification criteria, not A*/Dijkstra configuration or timing benchmarks.

Fixtures cover errors absent from the 1,000 reachable queries. Fixture weights are manually chosen; coordinates do not define fixture weights.
