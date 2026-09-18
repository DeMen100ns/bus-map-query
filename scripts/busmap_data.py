#!/usr/bin/env python3
"""Stable entrypoint; may be invoked by absolute path from any working directory."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from preprocessing.build_graph import GRAPH_PATH, RAW_PATH, init
from preprocessing.export_graph import MANIFEST_PATH, TEXT_PATH, export_graph
from verification.generate import BENCHMARK_DIR, generate
from verification.check import check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="Rebuild canonical graph from raw data")
    build.add_argument("--input", type=Path, default=RAW_PATH)
    build.add_argument("--output", type=Path, default=GRAPH_PATH)
    export = sub.add_parser("export", help="Export canonical graph to C++ text")
    export.add_argument("--input", type=Path, default=GRAPH_PATH)
    export.add_argument("--output", type=Path, default=TEXT_PATH)
    export.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    queries = sub.add_parser("queries", help="Generate the 1000-query suite and exact answers")
    queries.add_argument("--graph", type=Path, default=GRAPH_PATH)
    queries.add_argument("--output-dir", type=Path, default=BENCHMARK_DIR)
    queries.add_argument("--seed", type=int, default=162163)
    checker = sub.add_parser("check", help="Validate results; nonzero exit on failure")
    checker.add_argument("--graph", type=Path, default=GRAPH_PATH)
    checker.add_argument("--queries", type=Path, default=BENCHMARK_DIR / "queries.txt")
    checker.add_argument("--answers", type=Path, default=BENCHMARK_DIR / "answers.json")
    checker.add_argument("--results", type=Path, required=True)
    checker.add_argument("--mode", choices=["optimal", "any", "exact", "approximate"], default="optimal",
                         help="optimal: shortest path required; any: any valid path; exact/approximate are legacy aliases")
    args = parser.parse_args()
    try:
        if args.command == "build":
            report = init(args.input, args.output).metadata["statistics"]
        elif args.command == "export":
            report = export_graph(args.input, args.output, args.manifest)
        elif args.command == "queries":
            report = generate(args.graph, args.output_dir, args.seed)
        else:
            report = check(args.graph, args.queries, args.answers, args.results, args.mode)
        print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
        return 0 if report.get("ok", True) else 1
    except (ValueError, OSError, OverflowError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
