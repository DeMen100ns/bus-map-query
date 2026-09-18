# Data sources and reproducibility

## Dataset

BusMap uses a retained historical snapshot of bus route geometry in Ho Chi Minh City. The raw data is stored in [`data/raw/paths.jsonl`](../data/raw/paths.jsonl), with one route-variant record per line. It contains 297 records across 150 RouteId values.

The original collection date is unknown and is recorded as null in the export manifest. The repository does not identify a verified upstream download URL for this snapshot. It should not be presented as a current or live transit dataset.

Route coordinates describe path geometry rather than a verified set of bus stops. RouteId values are internal identifiers, not public route numbers. The dataset does not include schedules, waiting times, live traffic, or transfer costs.

## Canonical and exported data

The raw snapshot retains SHA-256:

```text
83875d9f19400cdd2ecd2f5df6a23ad92e223cb048cbcd4d9c95c17caf38b7dc
```

The canonical [`graph.json`](../data/processed/graph.json) records this source hash in its metadata. Normalization assigns dense IDs from exact coordinate tuples, removes self-loops, and merges duplicate directed edges by minimum weight. Nearby points are not snapped together. Comparisons with earlier graph representations must map through coordinates rather than assuming stable vertex IDs.

The C++ [`graph.txt`](../data/processed/graph.txt) is exported from the canonical JSON. The [`export manifest`](../data/processed/graph.manifest.json) records hashes for both representations, graph counts, source provenance, and generator versions. Normalization and export change the representation, not the age or real-world coverage of the snapshot.

See the [JSON schema](DATA_FORMAT.md) and [C++ text contract](CPP_FORMAT.md) for the complete specifications. Data preparation uses pyproj 3.8.0; generator metadata also records the PROJ version. Reproducible bytes depend on the recorded inputs and environment, and are not guaranteed across projection-library versions.

## Queries and benchmark evidence

The fixed [`benchmark query suite`](../benchmarks/README.md) is generated from this graph using seed 162163 and checked against an independent Python Dijkstra oracle. Its manifest records input identities and sampling parameters. It is a synthetic workload, not a record of real user journeys.

Committed benchmark JSON summaries preserve configurations, binary/data hashes, process measurements, and correctness results. Individual reports identify the implementation version and measurement protocol used. Older experiments remain evidence for design decisions rather than claims about the current implementation's performance.

Full local experiment sessions, binary snapshots, and repeated per-query outputs are stored under `artifacts/`, which is ignored by Git. Links into that directory refer to local experiment archives and are not downloadable files in the GitHub repository. The committed scripts can generate new sessions; timings depend on the machine and workload.

## Project author

BusMap is developed by **Vo Khac Trieu**. Current implementation details and limitations are documented in the [project report](../PROJECT_REPORT.md).
