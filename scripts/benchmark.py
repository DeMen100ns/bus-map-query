#!/usr/bin/env python3
"""Run C++ query timings, then verify every measured repetition outside the timer."""
import argparse
import csv
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from preprocessing.io_utils import json_text, sha256
from verification.check import check
from verification.formats import read_queries


def check_weighted_bound(result, weight):
    """Augment the path checker with the configured multiplicative cost bound."""
    limit_percent = (weight - 1) * 100
    violations = [gap["query_id"] for gap in result["gaps"]
                  if gap["gap_percent"] is None or gap["gap_percent"] > limit_percent + 1e-6]
    result["weighted_bound"] = {"weight": weight, "tolerance_percent": 1e-6,
                                "ok": not violations, "violations": violations}
    if violations:
        result["errors"].append(f"Weighted bound exceeded for query IDs: {violations}")
        result["ok"] = False


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return number


def summarize_ms(samples):
    samples = sorted(samples)
    return {"sample_count": len(samples), "mean_ms": math.fsum(samples) / len(samples),
            "p50_ms": samples[math.ceil(.50 * len(samples)) - 1],
            "p95_ms": samples[math.ceil(.95 * len(samples)) - 1],
            "max_ms": samples[-1], "total_query_ms": math.fsum(samples)}


def summarize_samples(path, query_ids, repetitions):
    samples, waits, latencies = [], [], []
    observed = set()
    with Path(path).open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        legacy = ["repetition", "query_id", "duration_ns", "status"]
        if reader.fieldnames not in (legacy, legacy + ["wait_ns", "latency_ns"]):
            raise ValueError("Unexpected timing CSV header")
        for row in reader:
            repetition, query_id, ns = (int(row[k]) for k in ("repetition", "query_id", "duration_ns"))
            wait = int(row.get("wait_ns", 0))
            latency = int(row.get("latency_ns", ns))
            key = (repetition, query_id)
            if (not 1 <= repetition <= repetitions or query_id not in query_ids or key in observed or ns < 0
                    or wait < 0 or latency < 0 or abs(latency - ns - wait) > 1
                    or row["status"] not in {"found", "unreachable", "invalid_vertex"}):
                raise ValueError("Invalid or duplicate timing sample")
            observed.add(key)
            samples.append(ns / 1e6)
            waits.append(wait / 1e6)
            latencies.append(latency / 1e6)
    if len(samples) != len(query_ids) * repetitions or not samples:
        raise ValueError("Missing timing samples")
    return {**summarize_ms(samples), "wait": summarize_ms(waits), "latency": summarize_ms(latencies)}


def summarize_batches(wall_times, query_count, repetitions):
    if (len(wall_times) != repetitions or query_count <= 0
            or any(not math.isfinite(value) or value <= 0 for value in wall_times)):
        raise ValueError("Invalid batch wall timings")
    total = math.fsum(wall_times)
    return {"mean_batch_wall_ms": total / repetitions,
            "min_batch_wall_ms": min(wall_times), "max_batch_wall_ms": max(wall_times),
            "throughput_qps": query_count * repetitions * 1000 / total,
            "run_throughput_qps": [query_count * 1000 / value for value in wall_times]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "build/busmap-bench")
    parser.add_argument("--graph", type=Path, default=ROOT / "data/processed/graph.json")
    parser.add_argument("--graph-text", type=Path, default=ROOT / "data/processed/graph.txt")
    parser.add_argument("--queries", type=Path, default=ROOT / "benchmarks/queries.txt")
    parser.add_argument("--answers", type=Path, default=ROOT / "benchmarks/answers.json")
    parser.add_argument("--algorithm", nargs="+", choices=["dijkstra", "dijkstra_baseline", "astar", "hpa", "bihpa"], default=["dijkstra", "astar"])
    parser.add_argument("--mode", choices=["optimal", "any"], help="default: any for HPA/BiHPA, optimal for other algorithms")
    parser.add_argument("--repetitions", type=positive, default=5)
    parser.add_argument("--warmup", type=nonnegative, default=1)
    parser.add_argument("--threads", type=positive, default=1)
    parser.add_argument("--queue-capacity", type=positive, default=64)
    parser.add_argument("--hpa-cluster-size", type=float)
    parser.add_argument("--hpa-weight", type=float)
    parser.add_argument("--hpa-diagnostics", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    try:
        hpa_explicit = args.hpa_cluster_size is not None or args.hpa_weight is not None or args.hpa_diagnostics
        if hpa_explicit and not set(args.algorithm) <= {"hpa", "bihpa"}:
            raise ValueError("Explicit HPA options require only hpa and/or bihpa algorithms")
        if args.hpa_cluster_size is not None and (not math.isfinite(args.hpa_cluster_size) or args.hpa_cluster_size <= 0):
            raise ValueError("HPA cluster size must be finite and positive")
        if args.hpa_weight is not None and (not math.isfinite(args.hpa_weight) or args.hpa_weight <= 1):
            raise ValueError("HPA weight must be finite and > 1")
        if len(set(args.algorithm)) != len(args.algorithm):
            raise ValueError("Algorithms must not repeat")
        args.binary = args.binary.resolve()
        if not args.binary.is_file():
            raise ValueError("Build busmap-bench first: cmake --build build --target busmap-bench")
        graph_hash = sha256(args.graph)
        query_hash, queries = read_queries(args.queries)
        if query_hash != graph_hash or not queries:
            raise ValueError("Query/graph mismatch or empty benchmark suite")
        answers = json.loads(args.answers.read_text(encoding="utf-8"))
        if answers.get("graph_sha256") != graph_hash or answers.get("queries_sha256") != sha256(args.queries):
            raise ValueError("Oracle files belong to a different graph/query set")
        created_at = datetime.now(timezone.utc).isoformat()
        session_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        output = (args.output_dir or ROOT / "artifacts/benchmarks" / session_name).resolve()
        output.mkdir(parents=True, exist_ok=False)
        report = {"schema_version": 2, "threads": args.threads, "queue_capacity": args.queue_capacity, "created_at_utc": created_at, "mode": args.mode or ("any" if set(args.algorithm) <= {"hpa", "bihpa"} else "auto" if set(args.algorithm) & {"hpa", "bihpa"} else "optimal"),
                  "measurement": "Service: worker wall time in router.query, including path reconstruction. Wait: submission overhead/backpressure/queue/scheduling. Latency: submission start to search completion, excludes delivery. Batch wall: scheduling through collecting all results, excludes graph load/setup, file I/O and validation.",
                  "workload": "Saturated finite batch with a bounded work queue; all paths retained until batch completion. Not a simulation of online arrival rates or CLI output latency.",
                  "percentiles": "Nearest-rank over all measured query calls (all repetitions pooled).",
                  "cache_policy": "No result cache. HPA reuses a read-only index with distances and precomputed shortcut paths. Warmup warms runtime/allocator; no cache reset hook is provided for future cached algorithms.",
                  "platform": platform.platform(), "machine": platform.machine(), "logical_cpu_count": os.cpu_count(),
                  "python_version": platform.python_version(), "binary_sha256": sha256(args.binary),
                  "graph_sha256": graph_hash, "graph_text_sha256": sha256(args.graph_text),
                  "queries_sha256": sha256(args.queries), "answers_sha256": sha256(args.answers),
                  "algorithms": {}, "ok": True}
        for algorithm in args.algorithm:
            mode = args.mode or ("any" if algorithm in ("hpa", "bihpa") else "optimal")
            directory = output / algorithm
            command = [str(args.binary), "--graph", str(args.graph_text.resolve()),
                       "--queries", str(args.queries.resolve()), "--algorithm", algorithm,
                       "--output-dir", str(directory), "--repetitions", str(args.repetitions), "--warmup", str(args.warmup),
                       "--threads", str(args.threads), "--queue-capacity", str(args.queue_capacity)]
            if algorithm in ("hpa", "bihpa"):
                if args.hpa_cluster_size is not None: command += ["--hpa-cluster-size", str(args.hpa_cluster_size)]
                if args.hpa_weight is not None: command += ["--hpa-weight", str(args.hpa_weight)]
                if args.hpa_diagnostics: command += ["--hpa-diagnostics"]
            print(f"Measuring {algorithm} with {args.threads} thread(s): {args.warmup} warmup + {args.repetitions} measured batches...", file=sys.stderr, flush=True)
            completed = subprocess.run(command, capture_output=True, text=True, timeout=300)
            if completed.returncode:
                report["algorithms"][algorithm] = {"ok": False, "error": completed.stderr.strip(), "exit_code": completed.returncode}
                report["ok"] = False
                continue
            timing = json.loads((directory / "timing.json").read_text())
            if (timing["schema_version"] != 2 or timing["graph_sha256"] != graph_hash
                    or timing["query_count"] != len(queries) or timing["repetitions"] != args.repetitions
                    or timing["warmup_batches"] != args.warmup or timing["threads"] != args.threads
                    or timing["queue_capacity"] != args.queue_capacity
                    or timing["executor"] != ("direct" if args.threads == 1 else "worker_pool")):
                raise ValueError("C++ timing metadata disagrees with the requested workload")
            if algorithm in ("hpa", "bihpa"):
                hpa = timing.get("hpa")
                if not hpa or (args.hpa_cluster_size is not None and hpa["cluster_size_m"] != args.hpa_cluster_size) or (args.hpa_weight is not None and hpa["heuristic_weight"] != args.hpa_weight):
                    raise ValueError("HPA metadata disagrees with requested options")
                if hpa.get("path_storage") != "paths":
                    raise ValueError("HPA path storage metadata disagrees with requested options")
            validation = []
            quality = None
            for repetition in range(1, args.repetitions + 1):
                result = check(args.graph, args.queries, args.answers, directory / f"results-{repetition}.txt", mode)
                if algorithm in ("hpa", "bihpa"):
                    check_weighted_bound(result, timing["hpa"]["heuristic_weight"])
                (directory / f"check-{repetition}.json").write_text(json_text(result))
                if repetition == 1:
                    gaps = sorted(g["gap_percent"] for g in result["gaps"] if g["gap_percent"] is not None)
                    quality = {"mean_gap_percent": result["mean_gap_percent"], "max_gap_percent": result["max_gap_percent"],
                               "p50_gap_percent": gaps[math.ceil(.5 * len(gaps))-1] if gaps else None,
                               "p95_gap_percent": gaps[math.ceil(.95 * len(gaps))-1] if gaps else None,
                               "optimal_found_count": result["optimal_found_count"], "valid_found_count": result["valid_found_count"],
                               "max_extra_distance_m": result["max_extra_distance_m"],
                               "undefined_relative_gap_count": result["undefined_relative_gap_count"]}
                validation.append({"repetition": repetition, "ok": result["ok"], "error_count": len(result["errors"]),
                                   "max_gap_percent": result["max_gap_percent"], "max_extra_distance_m": result["max_extra_distance_m"]})
            stats = summarize_samples(directory / "timings.csv", set(queries), args.repetitions)
            totals = timing["run_query_total_ms"]
            if (len(totals) != args.repetitions or any(not math.isfinite(t) or t < 0 for t in totals)
                    or not math.isclose(math.fsum(totals), stats["total_query_ms"], rel_tol=1e-9, abs_tol=1e-6)):
                raise ValueError("Service totals disagree with query samples")
            batch_stats = summarize_batches(timing["run_batch_wall_ms"], len(queries), args.repetitions)
            entry = {**timing, **stats, **batch_stats, "validation_mode": mode, "mean_batch_query_ms": math.fsum(totals) / len(totals),
                     "min_batch_query_ms": min(totals), "max_batch_query_ms": max(totals),
                     "validation": validation, "quality": quality, "ok": all(v["ok"] for v in validation)}
            report["algorithms"][algorithm] = entry
            report["ok"] &= entry["ok"]
        (output / "report.json").write_text(json_text(report))
        lines = ["# BusMap timing report", "",
                 f"Mode: `{report['mode']}`; {len(queries)} queries/batch; {args.repetitions} measured batches; "
                 f"{args.threads} thread(s); queue capacity {args.queue_capacity}.", "",
                 "| Algorithm | Batch wall (ms) | Throughput (query/s) | Mean service (ms) | p95 service (ms) | Mean wait (ms) | p95 latency (ms) | Validated |",
                 "|---|---:|---:|---:|---:|---:|---:|---|"]
        for algorithm, entry in report["algorithms"].items():
            if "mean_ms" not in entry:
                lines.append(f"| {algorithm} | — | — | — | — | — | — | ERROR |")
                continue
            lines.append(f"| {algorithm} | {entry['mean_batch_wall_ms']:.3f} | {entry['throughput_qps']:.1f} | "
                         f"{entry['mean_ms']:.4f} | {entry['p95_ms']:.4f} | {entry['wait']['mean_ms']:.4f} | "
                         f"{entry['latency']['p95_ms']:.4f} | {'PASS' if entry['ok'] else 'FAIL'} |")
        lines += ["", "Service measures router.query including path reconstruction. Wait includes submission overhead, queue backpressure and scheduling. Latency ends when search completes, before future delivery or ordered output.",
                  "Batch wall time includes submitting and collecting all queries; graph load, executor setup, file I/O and validation are outside it. Throughput = total measured queries / total batch wall time. Summed service time is not batch wall time with parallel workers.",
                  "One executor persists across warmup and repetitions. One thread uses direct calls; multiple threads use QueryPool. This is a saturated batch workload, not an online arrival-rate simulation; all result paths are retained until the batch ends.",
                  "Percentiles pool all calls across repetitions. Algorithms run sequentially in the listed order; host load and thermal state may affect results. Graph load is one observation per process and OS file cache is not cleared.",
                  "Without --mode, HPA/BiHPA use any (plus the configured weighted bound) and Dijkstra/A* use optimal. The selected optimal/any criterion does not change the search algorithm. A FAIL timing must not be presented as a successful solution benchmark.", ""]
        (output / "report.md").write_text("\n".join(lines))
        print("\n".join(lines))
        print(f"Report: {output / 'report.json'}")
        return 0 if report["ok"] else 1
    except (ValueError, OSError, subprocess.TimeoutExpired, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
