#!/usr/bin/env python3
"""Compare fresh-resource and reusable-workspace Dijkstra with full path validation."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys

from benchmark import ROOT, nonnegative, positive, summarize_ms


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "build/busmap-bench")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--processes", type=positive, default=4)
    parser.add_argument("--repetitions", type=positive, default=10)
    parser.add_argument("--warmup", type=nonnegative, default=2)
    parser.add_argument("--threads", type=positive, nargs="+", default=[1, 4])
    args = parser.parse_args()
    if len(set(args.threads)) != len(args.threads):
        parser.error("Worker counts must not repeat")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary = output / "busmap-bench"
    shutil.copy2(args.binary, binary)
    algorithms = ("dijkstra_baseline", "dijkstra")
    schedule = []
    for process in range(1, args.processes + 1):
        worker_order = args.threads if process % 2 else list(reversed(args.threads))
        algorithm_order = algorithms if process % 2 else tuple(reversed(algorithms))
        for workers in worker_order:
            schedule.extend((algorithm, workers, process) for algorithm in algorithm_order)
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
        "arguments": {key: str(value.resolve()) if isinstance(value, Path) else value
                      for key, value in vars(args).items()},
        "binary_sha256": sha256(binary), "queue_capacity": 64, "schedule": schedule,
        "input_sha256": {name: sha256(ROOT / name) for name in (
            "data/processed/graph.json", "data/processed/graph.txt", "benchmarks/queries.txt", "benchmarks/answers.json")},
        "protocol": "Independent sequential processes. Alternating baseline/optimized and worker-count order. "
                    "Service includes allocation/reset, search and complete path reconstruction. "
                    "All measured batches checked against the optimal oracle outside timers. "
                    "Result bytes must match between variants, processes and worker counts.",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    answers = json.loads((ROOT / "benchmarks/answers.json").read_text())["answers"]
    groups = {answer["query_id"]: answer["group"] for answer in answers}
    records, expected_hash = [], None
    for position, (algorithm, workers, process) in enumerate(schedule, 1):
        name = f"{algorithm}-t{workers}-p{process}"
        print(f"[{position}/{len(schedule)}] {name}", flush=True)
        destination = output / name
        command = [sys.executable, str(ROOT / "scripts/benchmark.py"), "--binary", str(binary),
                   "--algorithm", algorithm, "--mode", "optimal", "--threads", str(workers),
                   "--queue-capacity", "64", "--warmup", str(args.warmup), "--repetitions", str(args.repetitions),
                   "--output-dir", str(destination)]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=600)
        (output / f"{name}.log").write_text(completed.stdout + completed.stderr)
        if completed.returncode:
            raise RuntimeError(f"Benchmark/checker failed: {name}.log")
        report = json.loads((destination / "report.json").read_text())
        entry = report["algorithms"][algorithm]
        policy = "fresh_per_query" if algorithm == "dijkstra_baseline" else "reused_per_worker"
        if not report["ok"] or entry["build_type"] != "Release" or entry["dijkstra"]["resource_policy"] != policy:
            raise RuntimeError("Expected validated Release results with the requested Dijkstra resource policy")
        for result_file in (destination / algorithm).glob("results-*.txt"):
            digest = sha256(result_file)
            if expected_hash is not None and digest != expected_hash:
                raise RuntimeError(f"Different or nondeterministic paths: {name}")
            expected_hash = digest
        with (destination / algorithm / "timings.csv").open() as stream:
            samples = list(csv.DictReader(stream))
        entry["groups"] = {
            group: summarize_ms([int(row["duration_ns"]) / 1e6 for row in samples
                                 if groups[int(row["query_id"])] == group])
            for group in ("short", "medium", "long")}
        records.append({"algorithm": algorithm, "workers": workers, "process": process, "measurement": entry})
        (output / "processes.json").write_text(json.dumps(records, indent=2) + "\n")
        print(f"  PASS: {entry['mean_ms']*1000:.3f} us/query; {entry['throughput_qps']:.1f} query/s", flush=True)

    summaries = []
    for workers in args.threads:
        for algorithm in algorithms:
            entries = [r["measurement"] for r in records if (r["algorithm"], r["workers"]) == (algorithm, workers)]
            summary = {"algorithm": algorithm, "workers": workers}
            for key in ("mean_ms", "p50_ms", "p95_ms", "throughput_qps", "mean_batch_wall_ms", "peak_rss_bytes"):
                summary[key] = statistics.median(entry[key] for entry in entries)
            for key in ("mean_ms", "throughput_qps"):
                summary[key + "_min"] = min(entry[key] for entry in entries)
                summary[key + "_max"] = max(entry[key] for entry in entries)
            summary["groups"] = {
                group: {key: statistics.median(entry["groups"][group][key] for entry in entries)
                        for key in ("mean_ms", "p95_ms")}
                for group in ("short", "medium", "long")}
            summary["quality"] = entries[0]["quality"]
            summary["dijkstra"] = entries[0]["dijkstra"]
            summary["compiler"] = entries[0]["compiler"]
            summary["lto_enabled"] = entries[0]["lto_enabled"]
            summaries.append(summary)
    report = {"manifest": manifest, "summaries": summaries, "result_sha256": expected_hash,
              "all_measured_batches_valid": True,
              "aggregation": "Median of per-process mean/p95/throughput, each aggregated independently. "
                             "Quality counts each distinct query once. All process outliers retained."}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    flat_keys = [key for key, value in summaries[0].items() if not isinstance(value, dict)]
    with (output / "summary.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=flat_keys)
        writer.writeheader()
        writer.writerows({key: row[key] for key in flat_keys} for row in summaries)
    lines = ["# Dijkstra resource comparison", "", manifest["protocol"], "", report["aggregation"], "",
             "| Algorithm | Workers | Mean (us) | p95 (us) | Throughput (q/s) |",
             "|---|---:|---:|---:|---:|"]
    for row in summaries:
        lines.append(f"| {row['algorithm']} | {row['workers']} | {row['mean_ms']*1000:.3f} | "
                     f"{row['p95_ms']*1000:.3f} | {row['throughput_qps']:.1f} |")
    (output / "report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
