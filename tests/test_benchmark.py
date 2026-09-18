"""Timing harness tests, run by CTest; does not time or modify the user's solver logic."""
import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ARGUMENTS = sys.argv[1:] if __name__ == "__main__" else []


class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if len(ARGUMENTS) != 2:
            raise unittest.SkipTest("Run via CTest with benchmark executable and project root")
        cls.binary, cls.root = map(Path, ARGUMENTS)

    def command(self, output, algorithm="dijkstra"):
        return [str(self.binary), "--graph", str(self.root / "tests/fixtures/graph.txt"),
                "--queries", str(self.root / "tests/fixtures/queries.txt"), "--output-dir", str(output),
                "--algorithm", algorithm, "--repetitions", "2", "--warmup", "1",
                "--threads", "1", "--queue-capacity", "64"]

    def test_measurements_and_per_run_results(self):
        sys.path.insert(0, str(self.root / "python"))
        from verification.check import check
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "run"
            result = subprocess.run(self.command(output), capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            meta = json.loads((output / "timing.json").read_text())
            self.assertIsInstance(meta["lto_enabled"], bool)
            self.assertEqual((meta["query_count"], meta["repetitions"], meta["warmup_batches"]), (7, 2, 1))
            with (output / "timings.csv").open() as f:
                samples = list(csv.DictReader(f))
            self.assertEqual(len(samples), 14)
            self.assertEqual(len({(r['repetition'], r['query_id']) for r in samples}), 14)
            self.assertTrue(all(int(r["duration_ns"]) >= 0 for r in samples))
            for repetition in (1, 2):
                total_ms = sum(int(r['duration_ns']) / 1e6 for r in samples if int(r['repetition']) == repetition)
                self.assertAlmostEqual(total_ms, meta['run_query_total_ms'][repetition - 1])
                fixture = self.root / "tests/fixtures"
                self.assertTrue(check(fixture / "graph.json", fixture / "queries.txt", fixture / "answers.json",
                                      output / f"results-{repetition}.txt", "optimal")["ok"])

    def test_dijkstra_variants_runner_and_metadata(self):
        fixture = self.root / "tests/fixtures"
        outputs = []
        with tempfile.TemporaryDirectory() as tmp:
            for threads in (1, 4):
                output = Path(tmp) / str(threads)
                command = [sys.executable, str(self.root / "scripts/benchmark.py"), "--binary", str(self.binary),
                           "--graph", str(fixture / "graph.json"), "--graph-text", str(fixture / "graph.txt"),
                           "--queries", str(fixture / "queries.txt"), "--answers", str(fixture / "answers.json"),
                           "--algorithm", "dijkstra_baseline", "dijkstra", "--threads", str(threads),
                           "--queue-capacity", "1", "--warmup", "1", "--repetitions", "2", "--output-dir", str(output)]
                result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads((output / "report.json").read_text())
                self.assertTrue(report["ok"])
                for algorithm, policy, reset, storage in (
                    ("dijkstra_baseline", "fresh_per_query", "full", "local"),
                    ("dijkstra", "reused_per_worker", "touched", "workspace")):
                    entry = report["algorithms"][algorithm]
                    self.assertEqual(entry["validation_mode"], "optimal")
                    self.assertEqual(entry["dijkstra"], {"resource_policy": policy, "distance_reset": reset,
                                                          "heap_storage": storage})
                    self.assertIsNone(entry["hpa"])
                    for repetition in (1, 2):
                        outputs.append((output / algorithm / f"results-{repetition}.txt").read_bytes())
        self.assertTrue(all(value == outputs[0] for value in outputs))

    def test_parallel_measurements_match_serial(self):
        with tempfile.TemporaryDirectory() as tmp:
            outputs = []
            for threads in (1, 2, 4):
                output = Path(tmp) / str(threads)
                command = self.command(output)
                command[command.index("--threads") + 1] = str(threads)
                command[command.index("--queue-capacity") + 1] = "1"
                result = subprocess.run(command, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                meta = json.loads((output / "timing.json").read_text())
                self.assertEqual(meta["schema_version"], 2)
                self.assertEqual(meta["threads"], threads)
                self.assertEqual(meta["queue_capacity"], 1)
                self.assertEqual(meta["executor"], "direct" if threads == 1 else "worker_pool")
                with (output / "timings.csv").open() as f:
                    samples = list(csv.DictReader(f))
                self.assertEqual(len(samples), 14)
                for sample in samples:
                    service, wait, latency = (int(sample[k]) for k in ("duration_ns", "wait_ns", "latency_ns"))
                    self.assertGreaterEqual(service, 0)
                    self.assertGreaterEqual(wait, 0)
                    self.assertLessEqual(abs(latency - service - wait), 1)
                    if threads == 1:
                        self.assertEqual(wait, 0)
                for i, wall in enumerate(meta["run_batch_wall_ms"], 1):
                    self.assertGreater(wall, 0)
                    longest = max(int(s["latency_ns"]) for s in samples if int(s["repetition"]) == i) / 1e6
                    self.assertGreaterEqual(wall, longest)
                outputs.append([(output / f"results-{i}.txt").read_text() for i in (1, 2)])
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(outputs[0], outputs[2])

    def test_rejects_invalid_options_and_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name, args in [("zero", ["--repetitions", "0"]),
                               ("negative", ["--warmup", "-1"]),
                               ("threads", ["--threads", "0"]),
                               ("capacity", ["--queue-capacity", "0"]),
                               ("bad_threads", ["--threads", "-1"])]:
                command = self.command(base / name)
                for option, value in zip(args[::2], args[1::2]):
                    command[command.index(option) + 1] = value
                result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((base / name / "timing.json").exists())
            existing = base / "existing"
            existing.mkdir()
            marker = existing / "keep.txt"
            marker.write_text("keep")
            result = subprocess.run(self.command(existing), capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(marker.read_text(), "keep")

    def test_hpa_options_metadata_and_diagnostics(self):
        sys.path.insert(0, str(self.root / "python"))
        from verification.check import check
        with tempfile.TemporaryDirectory() as tmp:
            for algorithm, threads, weight in (("hpa", 1, 1.01), ("hpa", 4, 1.05),
                                                ("bihpa", 1, 1.01), ("bihpa", 4, 1.05)):
                output = Path(tmp) / f"{algorithm}-{threads}"
                command = self.command(output, algorithm)
                command[command.index("--threads") + 1] = str(threads)
                command += ["--hpa-cluster-size", "1", "--hpa-weight", str(weight), "--hpa-diagnostics"]
                result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                meta = json.loads((output / "timing.json").read_text())
                self.assertEqual(meta["hpa"]["cluster_size_m"], 1)
                self.assertEqual(meta["hpa"]["heuristic_weight"], weight)
                self.assertEqual(meta["hpa"]["path_storage"], "paths")
                self.assertGreater(meta["hpa"]["path_storage_bytes"], 0)
                self.assertNotIn("tree_count", meta["hpa"])
                self.assertNotIn("tree_parent_entries", meta["hpa"])
                self.assertNotIn("workspace_policy", meta["hpa"])
                self.assertNotIn("retained_hpa_workspace_bytes_per_worker", meta)
                self.assertGreater(meta["hpa"]["index_bytes"], 0)
                self.assertGreater(meta["max_hpa_workspace_bytes_per_worker"], 0)
                self.assertGreaterEqual(meta["router_setup_ms"], meta["hpa"]["index_build_ms"])
                self.assertEqual(meta["algorithm"], algorithm)
                self.assertEqual(meta["hpa"]["search_strategy"], "wbae" if algorithm == "bihpa" else "weighted_astar")
                self.assertEqual(meta["hpa"]["heuristic_cache"], "per_query")
                self.assertEqual(meta["hpa"]["heuristic_cache_invalidation"], "epoch" if algorithm == "bihpa" else "first_touch")
                self.assertEqual(meta["hpa"]["index_bytes"], meta["hpa"]["base_index_bytes"] + meta["hpa"]["reverse_index_bytes"])
                if algorithm == "bihpa":
                    self.assertGreater(meta["hpa"]["reverse_index_bytes"], 0)
                    self.assertGreater(meta["hpa"]["reverse_build_ms"], 0)
                fixture = self.root / "tests/fixtures"
                self.assertTrue(check(fixture / "graph.json", fixture / "queries.txt", fixture / "answers.json",
                                      output / "results-1.txt", "any")["ok"])
                with (output / "diagnostics.csv").open() as f:
                    rows = list(csv.DictReader(f))
                self.assertEqual(len(rows), 7)
                self.assertTrue(all(int(row["overlay_expanded"]) == int(row["forward_expanded"]) + int(row["backward_expanded"])
                                    for row in rows))
                self.assertTrue(all(int(r["reconstruction_ns"]) >= 0 for r in rows))
                self.assertTrue(all(0 <= int(r["heuristic_evaluations"]) <= int(r["heuristic_requests"]) for r in rows))
                self.assertGreater(sum(int(r["heuristic_evaluations"]) for r in rows), 0)
                self.assertNotIn("local_expanded", rows[0])
                self.assertNotIn("refinement_ns", rows[0])
            for i, options in enumerate((["--hpa-weight", "nan"], ["--hpa-weight", "0.9"],
                                         ["--hpa-cluster-size", "0"], ["--hpa-cluster-size", "inf"],
                                         ["--hpa-path-storage", "refine"], ["--hpa-path-storage", "trees"],
                                         ["--hpa-path-storage", "paths"], ["--hpa-weight", "1junk"],
                                         ["--hpa-weight", "1"], ["--hpa-workspace", "fresh"], ["--hpa-workspace", "reuse"])):
                result = subprocess.run(self.command(Path(tmp) / f"bad{i}", "hpa") + options,
                                        capture_output=True, text=True, timeout=15)
                self.assertNotEqual(result.returncode, 0)
            result = subprocess.run(self.command(Path(tmp) / "wrong") + ["--hpa-weight", "1"],
                                    capture_output=True, text=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)

    def test_runner_defaults_to_weighted_hpa(self):
        fixture = self.root / "tests/fixtures"
        with tempfile.TemporaryDirectory() as tmp:
            command = [sys.executable, str(self.root / "scripts/benchmark.py"), "--binary", str(self.binary),
                       "--graph", str(fixture / "graph.json"), "--graph-text", str(fixture / "graph.txt"),
                       "--queries", str(fixture / "queries.txt"), "--answers", str(fixture / "answers.json"),
                       "--algorithm", "hpa", "bihpa", "--hpa-cluster-size", "3500", "--repetitions", "1", "--warmup", "0", "--output-dir", tmp + "/run"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((Path(tmp) / "run/report.json").read_text())
            self.assertEqual(report["mode"], "any")
            self.assertEqual(report["algorithms"]["hpa"]["validation_mode"], "any")
            self.assertEqual(report["algorithms"]["hpa"]["hpa"]["cluster_size_m"], 3500)
            self.assertEqual(report["algorithms"]["hpa"]["hpa"]["heuristic_weight"], 1.05)
            self.assertEqual(report["algorithms"]["hpa"]["hpa"]["path_storage"], "paths")
            self.assertEqual(report["algorithms"]["bihpa"]["validation_mode"], "any")
            self.assertEqual(report["algorithms"]["bihpa"]["hpa"]["search_strategy"], "wbae")
            self.assertTrue(report["ok"])
            # Rejected before output creation or C++ launch.
            for options in (["--hpa-weight", "1"], ["--hpa-workspace", "fresh"],
                            ["--hpa-path-storage", "refine"], ["--hpa-path-storage", "trees"],
                            ["--hpa-path-storage", "paths"]):
                rejected = subprocess.run(command + options, capture_output=True, text=True, timeout=15)
                self.assertNotEqual(rejected.returncode, 0)

    def test_weighted_bound_validation(self):
        spec = importlib.util.spec_from_file_location("benchmark_runner", self.root / "scripts/benchmark.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for gap, expected in ((0, True), (1.0, True), (1.1, False), (None, False)):
            result = {"ok": True, "errors": [], "gaps": [{"query_id": 7, "gap_percent": gap}]}
            module.check_weighted_bound(result, 1.01)
            self.assertEqual(result["ok"], expected)
            self.assertEqual(result["weighted_bound"]["ok"], expected)

    def test_summary_statistics(self):
        spec = importlib.util.spec_from_file_location("benchmark_runner", self.root / "scripts/benchmark.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timings.csv"
            path.write_text("repetition,query_id,duration_ns,status\n1,0,1000000,found\n1,1,2000000,found\n2,0,3000000,found\n2,1,4000000,found\n")
            summary = module.summarize_samples(path, {0, 1}, 2)
            self.assertEqual(summary["mean_ms"], 2.5)
            self.assertEqual(summary["p50_ms"], 2)
            self.assertEqual(summary["p95_ms"], 4)
            self.assertEqual(summary["total_query_ms"], 10)
            with self.assertRaises(ValueError): module.summarize_samples(path, {0, 1, 2}, 2)
            # Throughput is derived from batch wall time, not summed query service.
            batches = module.summarize_batches([10, 20], 100, 2)
            self.assertAlmostEqual(batches["throughput_qps"], 200 * 1000 / 30)
            self.assertEqual(batches["mean_batch_wall_ms"], 15)
            for times in ([0, 10], [-1, 10], [float("nan"), 10], [10]):
                with self.assertRaises(ValueError): module.summarize_batches(times, 100, 2)
            path.write_text("repetition,query_id,duration_ns,status,wait_ns,latency_ns\n"
                            "1,0,1000000,found,2000000,3000000\n")
            summary = module.summarize_samples(path, {0}, 1)
            self.assertEqual(summary["mean_ms"], 1)
            self.assertEqual(summary["wait"]["mean_ms"], 2)
            self.assertEqual(summary["latency"]["mean_ms"], 3)
            path.write_text(path.read_text().replace("2000000,3000000", "2000000,1000000"))
            with self.assertRaises(ValueError): module.summarize_samples(path, {0}, 1)

    def test_runner_applies_both_correctness_modes(self):
        fixture = self.root / "tests/fixtures"
        with tempfile.TemporaryDirectory() as tmp:
            for mode in ("optimal", "any"):
                output = Path(tmp) / mode
                command = [sys.executable, str(self.root / "scripts/benchmark.py"), "--binary", str(self.binary),
                           "--graph", str(fixture / "graph.json"), "--graph-text", str(fixture / "graph.txt"),
                           "--queries", str(fixture / "queries.txt"), "--answers", str(fixture / "answers.json"),
                           "--algorithm", "dijkstra", "--mode", mode, "--repetitions", "1", "--warmup", "0",
                           "--output-dir", str(output), "--threads", "4", "--queue-capacity", "1"]
                result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads((output / "report.json").read_text())
                self.assertEqual(report["mode"], mode)
                self.assertTrue(report["ok"])
                self.assertEqual(report["threads"], 4)
                self.assertEqual(report["queue_capacity"], 1)
                entry = report["algorithms"]["dijkstra"]
                self.assertGreater(entry["throughput_qps"], 0)
                self.assertAlmostEqual(entry["throughput_qps"], 7 * 1000 / entry["mean_batch_wall_ms"])
                self.assertGreaterEqual(entry["latency"]["mean_ms"], entry["mean_ms"])
                self.assertEqual(report["algorithms"]["dijkstra"]["sample_count"], 7)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
