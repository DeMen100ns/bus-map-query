"""Check C++ paths against the canonical directed graph and Python oracle answers."""
import json
import math
from pathlib import Path

from preprocessing.build_graph import validate_document
from preprocessing.io_utils import sha256
from verification.formats import read_queries, read_results


def close(a, b):
    return abs(a - b) <= max(1e-6, 1e-9 * max(abs(a), abs(b)))


def check(graph_path, queries_path, answers_path, results_path, mode="optimal"):
    mode = {"exact": "optimal", "approximate": "any"}.get(mode, mode)
    if mode not in {"optimal", "any"}:
        raise ValueError("Unknown checker mode")
    document = json.loads(Path(graph_path).read_text(encoding="utf-8"))
    validate_document(document)
    digest = sha256(graph_path)
    query_digest, queries = read_queries(queries_path)
    result_digest, results = read_results(results_path)
    expected = json.loads(Path(answers_path).read_text(encoding="utf-8"))
    if expected.get("schema_version") != 1 or any(d != digest for d in
            (query_digest, result_digest, expected.get("graph_sha256"))):
        raise ValueError("Schema or graph hash mismatch")
    if expected.get("queries_sha256") != sha256(queries_path):
        raise ValueError("Answers belong to a different query file")
    answers = {}
    for answer in expected.get("answers", []):
        qid = answer.get("query_id")
        if type(qid) is not int or qid in answers:
            raise ValueError("Invalid or duplicate answer ID")
        status, distance = answer.get("status"), answer.get("distance_m")
        if status not in {"found", "unreachable", "invalid_vertex"}:
            raise ValueError("Invalid oracle status")
        if status == "found":
            if type(distance) not in (int, float) or not math.isfinite(distance) or distance < 0:
                raise ValueError("Invalid oracle distance")
        elif distance is not None:
            raise ValueError("Non-found oracle answer must have null distance")
        answers[qid] = answer
    if set(queries) != set(results) or set(queries) != set(answers):
        raise ValueError("Missing or extra query/result/answer IDs")
    weights = {(e["source"], e["target"]): e["distance_m"] for e in document["edges"]}
    errors, gaps = [], []
    for qid, (source, target) in queries.items():
        result, answer = results[qid], answers[qid]
        prefix = f"query {qid}: "
        invalid = not (0 <= source < len(document["nodes"]) and 0 <= target < len(document["nodes"]))
        if invalid != (answer["status"] == "invalid_vertex"):
            raise ValueError("Oracle invalid_vertex status disagrees with graph")
        if result["status"] == "not_implemented":
            errors.append(prefix + "not_implemented is not a successful answer")
            continue
        if result["status"] != answer["status"]:
            errors.append(prefix + "wrong reachability/status")
            continue
        if result["status"] != "found":
            continue
        path = result["path"]
        if (path[0], path[-1]) != (source, target) or any(v >= len(document["nodes"]) for v in path):
            errors.append(prefix + "invalid path endpoints/vertices")
            continue
        if source == target and path != [source]:
            errors.append(prefix + "self query must return the singleton path")
            continue
        pairs = list(zip(path, path[1:]))
        if any(pair not in weights for pair in pairs):
            errors.append(prefix + "path uses a missing directed edge")
            continue
        total = math.fsum(weights[pair] for pair in pairs)
        optimum = answer["distance_m"]
        if not close(total, result["distance_m"]):
            errors.append(prefix + "reported distance does not match path")
            continue
        if total < optimum and not close(total, optimum):
            errors.append(prefix + "path is shorter than oracle; investigate data/oracle")
            continue
        extra = max(0.0, total - optimum)
        is_optimal = close(total, optimum)
        relative_gap = max(0.0, (total / optimum - 1) * 100) if optimum > 0 else (0.0 if is_optimal else None)
        gaps.append({"query_id": qid, "gap_percent": relative_gap,
                     "extra_distance_m": extra, "optimal": is_optimal})
        if mode == "optimal" and not is_optimal:
            errors.append(prefix + "non-optimal distance")
    relative_gaps = [g["gap_percent"] for g in gaps if g["gap_percent"] is not None]
    return {"ok": not errors, "mode": mode, "query_count": len(queries), "errors": errors,
            "optimal_found_count": sum(g["optimal"] for g in gaps),
            "valid_found_count": len(gaps),
            "max_gap_percent": max(relative_gaps, default=None),
            "mean_gap_percent": math.fsum(relative_gaps) / len(relative_gaps) if relative_gaps else None,
            "undefined_relative_gap_count": len(gaps) - len(relative_gaps),
            "max_extra_distance_m": max((g["extra_distance_m"] for g in gaps), default=0.0),
            "gaps": gaps}
