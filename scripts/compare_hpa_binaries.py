#!/usr/bin/env python3
"""Compare two Release binaries on identical HPA queries, validating every measured batch."""
import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import random
import shutil
import statistics
import subprocess
import sys

from benchmark import ROOT, positive, nonnegative
from compare_bihpa import read_csv, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--queries", type=Path, default=ROOT / "benchmarks/queries.txt")
    parser.add_argument("--answers", type=Path, default=ROOT / "benchmarks/answers.json")
    parser.add_argument("--processes", type=positive, default=4)
    parser.add_argument("--repetitions", type=positive, default=8)
    parser.add_argument("--warmup", type=nonnegative, default=2)
    parser.add_argument("--threads", type=positive, nargs="+", default=[1, 4])
    parser.add_argument("--seed", type=int, default=162164)
    parser.add_argument("--hpa-cluster-size", type=float, default=3500)
    parser.add_argument("--hpa-weight", type=float, default=1.05)
    args = parser.parse_args()
    if len(set(args.threads)) != len(args.threads):
        parser.error("Worker counts must not repeat")
    if not math.isfinite(args.hpa_cluster_size) or args.hpa_cluster_size <= 0:
        parser.error("Cluster size must be finite and positive")
    if not math.isfinite(args.hpa_weight) or args.hpa_weight <= 1:
        parser.error("Weight must be finite and greater than one")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binaries = {}
    for version in ("before", "after"):
        binaries[version] = output / f"busmap-bench-{version}"
        shutil.copy2(getattr(args, version), binaries[version])
    rng = random.Random(args.seed)
    schedule = []
    for threads in args.threads:
        for algorithm in ("hpa", "bihpa"):
            orders = [process % 2 for process in range(args.processes)]
            rng.shuffle(orders)
            for process, reverse in enumerate(orders, 1):
                versions = ("after", "before") if reverse else ("before", "after")
                schedule.extend((algorithm, threads, process, version) for version in versions)
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
        "arguments": {key: str(value.resolve()) if isinstance(value, Path) else value for key, value in vars(args).items()},
        "binary_sha256": {version: sha256(binary) for version, binary in binaries.items()},
        "input_sha256": {name: sha256(path) for name, path in (
            ("queries", args.queries), ("answers", args.answers),
            ("graph_json", ROOT / "data/processed/graph.json"), ("graph_text", ROOT / "data/processed/graph.txt"))},
        "schedule": schedule,
        "protocol": "Independent sequential processes; balanced before/after order when process count is even. "
                    "Fixed L/w; service includes complete path reconstruction. All measured paths checked outside timers. "
                    "Diagnostics are separate passes. Result bytes must match before/after and across worker counts.",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    expected_hashes = {}

    def run(algorithm, threads, version, name, diagnostics=False):
        destination = output / name
        command = [sys.executable, str(ROOT / "scripts/benchmark.py"), "--binary", str(binaries[version]),
                   "--algorithm", algorithm, "--queries", str(args.queries.resolve()), "--answers", str(args.answers.resolve()),
                   "--threads", str(threads), "--queue-capacity", "64", "--output-dir", str(destination),
                   "--warmup", str(0 if diagnostics else args.warmup),
                   "--repetitions", str(1 if diagnostics else args.repetitions),
                   "--hpa-cluster-size", str(args.hpa_cluster_size), "--hpa-weight", str(args.hpa_weight)]
        if diagnostics:
            command.append("--hpa-diagnostics")
        completed = subprocess.run(command, capture_output=True, text=True, timeout=600)
        (output / f"{name}.log").write_text(completed.stdout + completed.stderr)
        if completed.returncode:
            raise RuntimeError(f"Benchmark or checker failed: {name}.log")
        entry = json.loads((destination / "report.json").read_text())["algorithms"][algorithm]
        if not entry["ok"] or entry["build_type"] != "Release":
            raise RuntimeError("Only validated Release results are accepted")
        for path in (destination / algorithm).glob("results-*.txt"):
            digest = sha256(path)
            if algorithm in expected_hashes and digest != expected_hashes[algorithm]:
                raise RuntimeError(f"Changed or nondeterministic paths for {algorithm}")
            expected_hashes[algorithm] = digest
        return entry

    records = []
    for position, (algorithm, threads, process, version) in enumerate(schedule, 1):
        name = f"{algorithm}-t{threads}-p{process}-{version}"
        print(f"[{position}/{len(schedule)}] {name}", flush=True)
        entry = run(algorithm, threads, version, name)
        records.append({"algorithm": algorithm, "threads": threads, "process": process, "version": version, "measurement": entry})
        (output / "processes.json").write_text(json.dumps(records, indent=2) + "\n")

    diagnostics = {}
    search_counters = ("query_id", "overlay_expanded", "forward_expanded", "backward_expanded", "edges_examined",
                       "stale_entries", "shortcuts_used", "peak_heap", "meeting_updates")
    for algorithm in ("hpa", "bihpa"):
        diagnostics[algorithm] = {}
        reference = None
        for version in ("before", "after"):
            name = f"diagnostics-{algorithm}-{version}"
            run(algorithm, 1, version, name, diagnostics=True)
            rows = read_csv(output / name / algorithm / "diagnostics.csv")
            counters = [tuple(row[key] for key in search_counters) for row in rows]
            if reference is not None and counters != reference:
                raise RuntimeError(f"Search behavior changed for {algorithm}")
            reference = counters
            keys = ("heuristic_requests", "heuristic_evaluations", "search_ns", "reconstruction_ns", "overlay_expanded")
            diagnostics[algorithm][version] = {key: statistics.mean(int(row[key]) for row in rows)
                                               for key in keys if key in rows[0]}

    summaries = []
    metrics = ("mean_ms", "p95_ms", "throughput_qps", "max_hpa_workspace_bytes_per_worker", "peak_rss_bytes")
    for threads in args.threads:
        for algorithm in ("hpa", "bihpa"):
            for version in ("before", "after"):
                entries = [record["measurement"] for record in records if record["algorithm"] == algorithm
                           and record["threads"] == threads and record["version"] == version]
                summary = {"algorithm": algorithm, "threads": threads, "version": version}
                summary.update({key: statistics.median(entry[key] for entry in entries) for key in metrics})
                summary.update({"mean_ms_min": min(entry["mean_ms"] for entry in entries),
                                "mean_ms_max": max(entry["mean_ms"] for entry in entries),
                                "index_bytes": entries[0]["hpa"]["index_bytes"]})
                summaries.append(summary)
    report = {"manifest": manifest, "summaries": summaries, "diagnostics": diagnostics, "result_sha256": expected_hashes}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    with (output / "summary.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    lines = ["# HPA binary comparison", "", manifest["protocol"], "",
             "Metrics are medians across processes; p95 within each process pools measured query calls.", "",
             "| Algorithm | Threads | Version | Mean (µs) | p95 (µs) | Throughput (q/s) | Workspace/worker (bytes) |",
             "|---|---:|---|---:|---:|---:|---:|"]
    for row in summaries:
        lines.append(f"| {row['algorithm']} | {row['threads']} | {row['version']} | {row['mean_ms']*1000:.3f} | "
                     f"{row['p95_ms']*1000:.3f} | {row['throughput_qps']:.1f} | {row['max_hpa_workspace_bytes_per_worker']:.0f} |")
    (output / "report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
