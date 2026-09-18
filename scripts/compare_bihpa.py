#!/usr/bin/env python3
"""Compare HPA and BiHPA sequentially, with balanced process order and full validation."""
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

from benchmark import ROOT, positive, nonnegative, summarize_ms


def percentile(values, fraction):
    return sorted(values)[math.ceil(len(values) * fraction) - 1]


def sha256(path):
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "build/busmap-bench")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--processes", type=positive, default=6)
    parser.add_argument("--repetitions", type=positive, default=10)
    parser.add_argument("--warmup", type=nonnegative, default=2)
    parser.add_argument("--threads", nargs="+", type=positive, default=[1, 4])
    parser.add_argument("--seed", type=int, default=162164)
    parser.add_argument("--hpa-cluster-size", type=float, default=3500)
    parser.add_argument("--hpa-weight", type=float, default=1.05)
    args = parser.parse_args()
    if not math.isfinite(args.hpa_cluster_size) or args.hpa_cluster_size <= 0:
        parser.error("Cluster size must be finite and positive")
    if not math.isfinite(args.hpa_weight) or args.hpa_weight <= 1:
        parser.error("Weight must be finite and > 1")
    if len(set(args.threads)) != len(args.threads):
        parser.error("Worker counts must not repeat")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary = output / "busmap-bench"
    shutil.copy2(args.binary, binary)
    answers = json.loads((ROOT / "benchmarks/answers.json").read_text())["answers"]
    groups = {answer["query_id"]: answer["group"] for answer in answers}
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
        "binary_sha256": sha256(binary), "arguments": {key: str(value) if isinstance(value, Path) else value
                                                       for key, value in vars(args).items()},
        "files": {name: sha256(ROOT / name) for name in (
            "data/processed/graph.json", "data/processed/graph.txt", "benchmarks/queries.txt", "benchmarks/answers.json",
            "cpp/src/bihpa.cpp", "cpp/include/busmap/bihpa.hpp", "scripts/compare_bihpa.py")},
        "protocol": "Fixed configuration; no tuning on the evaluation suite. Sequential independent processes. "
                    "Balanced AB/BA order within each worker count. Checker and file I/O outside timers. "
                    "Quality uses one sample per query. Diagnostics run after all latency processes.",
    }
    schedule = []
    random_generator = random.Random(args.seed)
    for workers in args.threads:
        orders = [index % 2 for index in range(args.processes)]
        random_generator.shuffle(orders)
        for process, reverse in enumerate(orders, 1):
            algorithms = ["bihpa", "hpa"] if reverse else ["hpa", "bihpa"]
            schedule.extend((workers, process, algorithm) for algorithm in algorithms)
    manifest["schedule"] = schedule
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    def run(algorithm, workers, destination, diagnostic=False):
        command = [sys.executable, str(ROOT / "scripts/benchmark.py"), "--binary", str(binary),
                   "--algorithm", algorithm, "--threads", str(workers), "--queue-capacity", "64",
                   "--warmup", str(0 if diagnostic else args.warmup),
                   "--repetitions", str(1 if diagnostic else args.repetitions),
                   "--hpa-cluster-size", str(args.hpa_cluster_size), "--hpa-weight", str(args.hpa_weight),
                   "--output-dir", str(destination)]
        if diagnostic:
            command.append("--hpa-diagnostics")
        completed = subprocess.run(command, capture_output=True, text=True, timeout=600)
        (output / f"{destination.name}.log").write_text(completed.stdout + completed.stderr)
        if completed.returncode:
            raise RuntimeError(f"Benchmark/checker failed; see {destination.name}.log")
        report = json.loads((destination / "report.json").read_text())
        entry = report["algorithms"][algorithm]
        if not report["ok"] or entry["build_type"] != "Release":
            raise RuntimeError("Only validated Release measurements are accepted")
        return entry

    records = []
    result_hashes = {}
    for position, (workers, process, algorithm) in enumerate(schedule, 1):
        print(f"[{position}/{len(schedule)}] {algorithm}, workers={workers}, process={process}", flush=True)
        destination = output / f"{algorithm}-t{workers}-p{process}"
        entry = run(algorithm, workers, destination)
        directory = destination / algorithm
        for result_file in directory.glob("results-*.txt"):
            digest = sha256(result_file)
            if algorithm in result_hashes and result_hashes[algorithm] != digest:
                raise RuntimeError(f"Nondeterministic results for {algorithm}")
            result_hashes[algorithm] = digest
        rows = read_csv(directory / "timings.csv")
        entry["groups"] = {}
        for group in ("short", "medium", "long"):
            samples = [int(row["duration_ns"]) / 1e6 for row in rows if groups[int(row["query_id"])] == group]
            entry["groups"][group] = summarize_ms(samples)
        records.append({"algorithm": algorithm, "workers": workers, "process": process, "measurement": entry})
        (output / "processes.json").write_text(json.dumps(records, indent=2) + "\n")

    diagnostics = {}
    for algorithm in ("hpa", "bihpa"):
        destination = output / f"diagnostics-{algorithm}"
        run(algorithm, 1, destination, diagnostic=True)
        rows = read_csv(destination / algorithm / "diagnostics.csv")
        diagnostics[algorithm] = {key: statistics.mean(int(row[key]) for row in rows) for key in (
            "overlay_expanded", "forward_expanded", "backward_expanded", "edges_examined", "stale_entries",
            "shortcuts_used", "peak_heap", "search_ns", "reconstruction_ns", "meeting_updates")}

    summaries = []
    for workers in args.threads:
        for algorithm in ("hpa", "bihpa"):
            entries = [record["measurement"] for record in records
                       if record["algorithm"] == algorithm and record["workers"] == workers]
            summary = {"algorithm": algorithm, "workers": workers}
            for key in ("mean_ms", "p95_ms", "throughput_qps", "graph_load_ms", "router_setup_ms", "peak_rss_bytes",
                        "max_hpa_workspace_bytes_per_worker"):
                summary[key] = statistics.median(entry[key] for entry in entries)
            summary["mean_ms_min"] = min(entry["mean_ms"] for entry in entries)
            summary["mean_ms_max"] = max(entry["mean_ms"] for entry in entries)
            summary["hpa"] = {key: statistics.median(entry["hpa"][key] for entry in entries) for key in (
                "index_build_ms", "base_index_build_ms", "reverse_build_ms", "index_bytes", "base_index_bytes",
                "reverse_index_bytes", "path_storage_bytes", "path_nodes")}
            summary["quality"] = entries[0]["quality"]
            summary["groups"] = {group: {key: statistics.median(entry["groups"][group][key] for entry in entries)
                                          for key in ("mean_ms", "p95_ms")} for group in ("short", "medium", "long")}
            gaps = json.loads((output / f"{algorithm}-t{workers}-p1" / algorithm / "check-1.json").read_text())["gaps"]
            for group in ("short", "medium", "long"):
                group_gaps = [gap["gap_percent"] for gap in gaps if groups[gap["query_id"]] == group]
                summary["groups"][group].update({"mean_gap_percent": statistics.mean(group_gaps),
                                                "p95_gap_percent": percentile(group_gaps, .95),
                                                "max_gap_percent": max(group_gaps)})
            summaries.append(summary)
    report = {"manifest": manifest, "summaries": summaries, "diagnostics": diagnostics, "result_sha256": result_hashes}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    flat_keys = [key for key, value in summaries[0].items() if not isinstance(value, dict)]
    with (output / "summary.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=flat_keys)
        writer.writeheader()
        writer.writerows({key: summary[key] for key in flat_keys} for summary in summaries)
    lines = ["# HPA versus bidirectional HPA", "", manifest["protocol"], "",
             "Values below are medians across processes. Service includes full path reconstruction.", "",
             "| Algorithm | Workers | Mean service (µs) | p95 service (µs) | Throughput (q/s) | Index (bytes) | Workspace/worker (bytes) |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for row in summaries:
        lines.append(f"| {row['algorithm']} | {row['workers']} | {row['mean_ms']*1000:.3f} | {row['p95_ms']*1000:.3f} | "
                     f"{row['throughput_qps']:.1f} | {row['hpa']['index_bytes']:.0f} | {row['max_hpa_workspace_bytes_per_worker']:.0f} |")
    lines += ["", "Raw timings, per-repetition paths/checker output, groups, quality, diagnostics and hashes accompany this report.", ""]
    (output / "report.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
