# Sources and preservation records

## Task 2 files

The Task 2 source and data originated in `modules/cs163/task02-routing/` before cleanup. After normalization, the raw data is stored at `data/raw/paths.jsonl`, with its original bytes preserved; the algorithm graph is the rebuilt `data/processed/graph.json`. The current source includes builder, reader, and main-guard updates; the entire Python source is no longer claimed to be byte-identical.

The report `docs/course-reports/cs163/task02-routing/Report Solo Project Task 2.pdf` was moved and renamed to `docs/task2-original-report.pdf`; its PDF content is unchanged.

Assignment author: **Vo Khac Trieu — 23125020**, CS163 Solo Project, Task 2. The original report is a reference for methods and historical benchmarks; its timings were not reproduced during this cleanup.

## Earlier submission links

The following two URLs were extracted from old ReadMe files. Their Google Drive contents have not been checked, so neither link is claimed to contain Task 2 exclusively. They preserve source references after removal of the surrounding folders and ZIP archives.

- [archive/cs163/submission-links/23125020_01/ReadMe.txt](https://drive.google.com/drive/folders/1cqDRW8tAlkylZmRKC9UeqEQnYQGpnjWl?usp=sharing)
- [archive/cs163/submission-links/23125020_03/ReadMe.txt](https://drive.google.com/drive/folders/1jmJON5cOP3EcgBXXIVxzLqHaC7yTzw46?usp=sharing)

## Backup before consolidation

A complete copy of the BusMap tree before cleanup is stored at:

[BusMap before cleanup](</Users/demen100ns/Documents/Code Training/Project/BusMap-backup-before-task2-20260917/original>)

It contains all source, datasets, reports, archives, and `.git` metadata from before the changes. This copy is outside the main project and is not a dependency for running Task 2.

- [Pre-cleanup file manifest](</Users/demen100ns/Documents/Code Training/Project/BusMap-backup-before-task2-20260917/cleanup-manifest.json>) — relative paths, sizes, SHA-256 hashes, and mappings of retained files.
- [Verification results](</Users/demen100ns/Documents/Code Training/Project/BusMap-backup-before-task2-20260917/verification.json>) — preservation, data, and structural checks.

The earlier Task 2 cleanup preserved source code, data, and Git metadata byte for byte. The subsequent normalization changed source files and rebuilt the graph, as described in the [current report](</Users/demen100ns/Documents/Code Training/Project/BusMap/PROJECT_REPORT.md>).

Consolidation reduced the size of the working project; the backup still occupies disk space. It does not need to be included in the new development copy. To restore files, retrieve them from `original/` using the paths in the manifest; do not automatically overwrite newer changes.

## Backup before data normalization v1

The [project before normalization](</Users/demen100ns/Documents/Code Training/Project/BusMap-backup-before-data-v1-20260917/original>) retains `python/Graph.txt`, `python/paths.json`, source, and documentation from before the format change.

- [Pre-change manifest and hashes](</Users/demen100ns/Documents/Code Training/Project/BusMap-backup-before-data-v1-20260917/before.json>).
- [Migration verification results](</Users/demen100ns/Documents/Code Training/Project/BusMap-backup-before-data-v1-20260917/verification.json>).

`paths.jsonl` retains its SHA-256 hash: `83875d9f19400cdd2ecd2f5df6a23ad92e223cb048cbcd4d9c95c17caf38b7dc`. The new graph JSON records this hash in its metadata. Vertex IDs were reassigned using coordinate tuples; comparisons with old data must use coordinates, not IDs alone.

The graph was built and tests were run with pyproj 3.8.0 in a temporary virtual environment, without installing it into system Python. Benchmarks had not yet been rerun and C++ had not yet been migrated. [DATA_FORMAT.md](</Users/demen100ns/Documents/Code Training/Project/BusMap/docs/DATA_FORMAT.md>) is the current data specification.

## C++ and data handoff (2026-09-17)

No new bus data was collected in this phase. Raw data and canonical graph JSON remain byte-identical; the graph text and query suite were generated from that snapshot. The snapshot collection date is unknown and is recorded as null in the export manifest.

The pre-change backup is outside the repository at `../BusMap-backup-before-cpp-handoff-20260917/original/`, with an inventory/hashes and verification results. Git metadata is unchanged. Python reference code changed only imports/paths; the C++ algorithms are stubs for the user to implement.
