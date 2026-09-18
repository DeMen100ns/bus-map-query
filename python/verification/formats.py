"""Versioned line-based query/result formats shared with the C++ CLI."""
import math
import re
from pathlib import Path


def integer(token, minimum=0, maximum=(1 << 64) - 1):
    if not re.fullmatch(r"-?[0-9]+", token):
        raise ValueError(f"Invalid integer: {token}")
    value = int(token)
    if not minimum <= value <= maximum:
        raise ValueError(f"Integer out of range: {token}")
    return value


def read_records(path, magic, count_label):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if len(lines) < 3 or lines[0].split() != [magic, "1"]:
        raise ValueError(f"Expected {magic} 1")
    header = lines[1].split()
    if len(header) != 2 or header[0] != "GRAPH_SHA256" or not re.fullmatch(r"[0-9a-f]{64}", header[1]):
        raise ValueError("Invalid GRAPH_SHA256")
    count = lines[2].split()
    if len(count) != 2 or count[0] != count_label:
        raise ValueError(f"Expected {count_label}")
    size = integer(count[1])
    if len(lines) != size + 3:
        raise ValueError("Missing or extra records")
    return header[1], [line.split() for line in lines[3:]]


def read_queries(path):
    digest, records = read_records(path, "BUSMAP_QUERIES", "QUERIES")
    queries = {}
    for fields in records:
        if len(fields) != 3:
            raise ValueError("Query requires id, source, target")
        qid = integer(fields[0])
        if qid in queries:
            raise ValueError("Duplicate query ID")
        queries[qid] = tuple(integer(v, -(1 << 63), (1 << 63) - 1) for v in fields[1:])
    return digest, queries


def read_results(path):
    digest, records = read_records(path, "BUSMAP_RESULTS", "RESULTS")
    results = {}
    for fields in records:
        if len(fields) < 4:
            raise ValueError("Incomplete result")
        qid, status, raw_distance, size = fields[:4]
        qid, size = integer(qid), integer(size)
        if qid in results or len(fields) != size + 4:
            raise ValueError("Duplicate result ID or wrong path length")
        if status not in {"found", "unreachable", "invalid_vertex", "not_implemented"}:
            raise ValueError("Unknown result status")
        if status == "found":
            distance = float(raw_distance)
            if not math.isfinite(distance) or distance < 0 or size == 0:
                raise ValueError("Found result requires a finite nonnegative distance and nonempty path")
        else:
            if raw_distance != "-" or size != 0:
                raise ValueError("Non-found result requires '- 0'")
            distance = None
        results[qid] = {"status": status, "distance_m": distance,
                        "path": [integer(v, maximum=(1 << 32) - 1) for v in fields[4:]]}
    return digest, results


def query_text(digest, queries):
    lines = ["BUSMAP_QUERIES 1", f"GRAPH_SHA256 {digest}", f"QUERIES {len(queries)}"]
    lines.extend(f"{qid} {source} {target}" for qid, (source, target) in queries.items())
    return "\n".join(lines) + "\n"
