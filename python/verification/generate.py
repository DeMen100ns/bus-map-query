"""Reproducible, stratified HCMC query suite and exact oracle distances."""
import json
import math
from pathlib import Path
import random

from preprocessing.build_graph import GRAPH_PATH, validate_document
from preprocessing.io_utils import ROOT, atomic_text, json_text, protect_outputs, sha256
from verification.formats import query_text
from verification.oracle import adjacency, shortest_paths

BENCHMARK_DIR = ROOT / "benchmarks"


def generate(input_path=GRAPH_PATH, output_dir=BENCHMARK_DIR, seed=162163, excluded_sources=None):
    output_dir = Path(output_dir)
    queries_path = output_dir / "queries.txt"
    answers_path = output_dir / "answers.json"
    manifest_path = output_dir / "manifest.json"
    protect_outputs([queries_path, answers_path, manifest_path], [input_path, GRAPH_PATH])
    document = json.loads(Path(input_path).read_text(encoding="utf-8"))
    validate_document(document)
    graph = adjacency(document)
    randomizer = random.Random(seed)
    excluded = set(excluded_sources or ())
    sources = [u for u in range(len(graph)) if u not in excluded]
    randomizer.shuffle(sources)
    pool = []
    for source in sources:
        distances, _ = shortest_paths(graph, source)
        reachable = [v for v, d in enumerate(distances) if v != source and math.isfinite(d)]
        for target in randomizer.sample(reachable, min(10, len(reachable), 3000 - len(pool))):
            pool.append((distances[target], source, target))
        if len(pool) == 3000:
            break
    if len(pool) != 3000:
        raise ValueError("Dataset cannot supply the required 3000-pair pool with at most 10 targets per source")
    pool.sort()
    selected = []
    groups = {}
    for index, (name, count) in enumerate((("short", 334), ("medium", 333), ("long", 333))):
        candidates = pool[index * 1000:(index + 1) * 1000]
        sample = sorted(randomizer.sample(candidates, count))
        selected.extend((name, distance, source, target) for distance, source, target in sample)
        groups[name] = {"count": count, "pool_min_m": candidates[0][0], "pool_max_m": candidates[-1][0]}
    randomizer.shuffle(selected)
    queries = {i: (source, target) for i, (_, _, source, target) in enumerate(selected)}
    digest = sha256(input_path)
    atomic_text(queries_path, query_text(digest, queries))
    query_digest = sha256(queries_path)
    answers = {"schema_version": 1, "graph_sha256": digest, "queries_sha256": query_digest,
               "answers": [{"query_id": i, "status": "found", "distance_m": distance, "group": name}
                           for i, (name, distance, _, _) in enumerate(selected)]}
    atomic_text(answers_path, json_text(answers))
    manifest = {"schema_version": 1, "generator": "busmap-data queries v1", "seed": seed,
                "graph_sha256": digest, "node_count": len(graph), "pool_size": 3000,
                "query_count": 1000, "max_targets_per_source": 10, "groups": groups,
                "sampling": "Shuffled sources over the whole ID domain; reachable non-self targets; distance tertiles of the pool, not population quantiles.",
                "files": {"queries.txt": query_digest, "answers.json": sha256(answers_path)}}
    if excluded:
        manifest["excluded_sources"] = sorted(excluded)
    atomic_text(manifest_path, json_text(manifest))
    return manifest
