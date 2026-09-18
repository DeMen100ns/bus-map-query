# BusMap graph schema v1

## 1. Files

- `data/raw/paths.jsonl`: original data with one JSON object per line, preserving the content of the old `paths.json`.
- `data/processed/graph.json`: one complete JSON object containing metadata, vertices, and edges.

Raw data retains the `lng` field and string route IDs. Normalized data uses `lon` and integer vertex IDs. The original is not modified merely to standardize field names.

## 2. Raw JSONL

Each line contains nonempty string `RouteId` and `RouteVarId` fields, plus numeric `lat` and `lng` arrays of equal, nonzero length. Point i is `(lat[i], lng[i])`; array order defines path direction.

The builder rejects nonfinite coordinates, latitude outside [-90, 90], longitude outside [-180, 180], unequal array lengths, and records with missing fields. It does not silently truncate mismatched arrays with `zip`.

## 3. Graph JSON

This example illustrates only the schema; the weight is not derived from the example coordinates:

```json
{
  "schema_version": 1,
  "directed": true,
  "coordinate_crs": "EPSG:4326",
  "distance_crs": "EPSG:3405",
  "weight_unit": "meter",
  "nodes": [
    {"id": 0, "lat": 10.77, "lon": 106.70},
    {"id": 1, "lat": 10.78, "lon": 106.71}
  ],
  "edges": [
    {"source": 0, "target": 1, "distance_m": 1550.0}
  ]
}
```

### Required header

| Field | v1 convention |
|---|---|
| `schema_version` | Integer 1 |
| `directed` | Boolean `true` |
| `coordinate_crs` | `EPSG:4326`, geographic coordinates |
| `distance_crs` | `EPSG:3405`, VN-2000 / UTM zone 48N |
| `weight_unit` | `meter` |
| `nodes` | Nonempty vertex array |
| `edges` | Edge array, possibly empty |

### Vertices

Each vertex has an integer `id` and finite `lat` and `lon`. Vertices are sorted ascending by `(lat, lon)` and assigned consecutive IDs starting at 0. `nodes[i].id == i`. Each coordinate pair appears exactly once.

There is no rounding, tolerance, or snapping of nearby points. Treating nearby points as one intersection would be a separate modeling change.

### Edges

Each edge has existing `source` and `target` IDs and a finite, nonnegative `distance_m`. The two IDs must differ. A directed pair appears only once; edges are sorted ascending by `(source, target)`.

An edge `u → v` does not implicitly create `v → u`. If both directions occur in the raw data, both are retained. The schema permits zero-weight edges between distinct vertices; do not arbitrarily filter them with `weight > 0`.

### Additional builder metadata

- `distance_method`: `euclidean_projected`.
- `node_id_policy`: `dense_ids_sorted_by_lat_lon`.
- `coordinate_merge_policy`: `exact_pair_no_rounding_or_snapping`.
- `edge_policy`: remove self-loops and retain the minimum weight for duplicate directed pairs.
- `source`: input data path and SHA-256.
- `generator`: builder name/version and pyproj/PROJ versions.
- `statistics`: input and output counts. The reader checks node/edge counts when this section is present.

This is a distance graph. The current schema does not carry route membership on edges, schedules, waiting times, or transfer information. Adding them requires clearly defined semantics and version compatibility.

## 4. Construction rules

1. Validate every JSONL record.
2. Collect exact coordinate pairs and sort by latitude, then longitude.
3. Assign IDs `0…N−1`, using coordinate tuples as keys instead of `lat × 10⁹ + lon`.
4. Project each vertex with `Transformer.from_crs(..., always_xy=True)`, passing `(lon, lat)`.
5. Connect consecutive points in each route in their original direction.
6. Remove segments whose endpoints have the same coordinates/ID.
7. Compute Euclidean distance in projected coordinates, in meters.
8. Merge duplicate directed edge pairs using the minimum distance; do not add reverse edges.
9. Check invariants, write JSON to a temporary file, then replace the destination.

Data may contain a point that appears alone or participates only in self-loops. It is retained as an isolated vertex. Vertices are not deleted just because filtering leaves them without edges.

## 5. Current output statistics

| Quantity | Count |
|---|---:|
| Route records | 297 |
| Coordinate samples | 73,735 |
| Segments between consecutive samples | 73,438 |
| Self-loop segments removed, counted in raw data | 18,454 |
| Additional duplicate directed segments merged | 12,814 |
| Unique vertices / IDs after normalization | 38,148 |
| Edges after normalization | 42,170 |

`73,438 − 18,454 − 12,814 = 42,170`.

Counts of removed raw segments are not the self-loop count in the old `Graph.txt`, because the old graph had already performed some deduplication.

## 6. Differences from the old data

| Property | Old Graph.txt | graph.json v1 |
|---|---|---|
| Format | Text mixing numbers and line-based JSON | One versioned JSON object |
| Coordinates in edges | Repeated objects for both vertices | ID references only |
| Vertex records | 38,148 | 38,148 |
| Unique IDs | 38,147 | 38,148 |
| Stored edges | 46,484, including self-loops | 42,170, without self-loops |
| Duplicate / missing IDs | Duplicate 7694, missing 7693 | No duplicates or omissions |
| Unit/CRS convention | Implicit in code | Explicit in the file |
| Rebuilt content | Dependent on the old ID assignment bug and reader | Based on coordinate tuples and a verifiable schema |

The old code removed self-loops by ID before searching, leaving 42,169 edge records to traverse. Comparing 46,484 with 42,170 does not imply that many useful connections were lost: most were self-loops, and the old ID issue also affects how connections are interpreted.

**Do not migrate old results or queries to the new graph by ID alone.** Comparisons must map through coordinates; old IDs are not one-to-one. The old `Graph.txt` is retained in the pre-normalization backup.

## 7. C++ and the current pipeline

Python additionally exports a versioned graph.txt with x/y coordinates in meters and a manifest; JSON v1 remains canonical. The C++ loader is implemented and builds read-only CSR. See the [C++ contract](CPP_FORMAT.md) and [architecture](ARCHITECTURE.md).

The Python entry point is `scripts/busmap_data.py`: build, export, queries, check. The builder is currently at `python/preprocessing/build_graph.py`; the historical generator name in JSON is retained to avoid changing canonical bytes solely because code moved.

## 8. Verification and limitations

Tests check formerly colliding IDs, directed edges, distances in meters, complete coordinate/edge sets, input hashes, and reproducible JSON rebuilds and text exports in the recorded environment. Historical Python modules still read JSON through the preprocessing loader.

C++ queries use text to avoid a JSON dependency; the oracle/checker reads JSON independently. The C++ text export preserves the canonical graph without modifying the raw snapshot. The collection date is unknown. The reader does not access raw data to verify hashes on every query. Byte-identical output is not guaranteed across library/projection versions. C++ implements Dijkstra, A*, Weighted HPA*, and bidirectional Weighted HPA*. The historical Python A*/hierarchical implementations remain references with known limitations; verification uses the independent Python oracle.
