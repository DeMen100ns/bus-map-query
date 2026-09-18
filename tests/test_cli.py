"""CTest integration tests for input validation and HPA integration."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CLI_ARGUMENTS = sys.argv[1:] if __name__ == "__main__" else []


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if len(CLI_ARGUMENTS) != 2:
            raise unittest.SkipTest("Run via CTest with executable and project paths")
        cls.binary, cls.root = map(Path, CLI_ARGUMENTS)
        cls.fixture = cls.root / "tests/fixtures"

    def run_cli(self, graph=None, queries=None, algorithm="dijkstra", stdin=None):
        command = [str(self.binary), "--graph", str(graph or self.fixture / "graph.txt"),
                   "--algorithm", algorithm]
        if stdin is None:
            command.extend(["--queries", str(queries or self.fixture / "queries.txt")])
        return subprocess.run(command, input=stdin, text=True, capture_output=True, cwd=tempfile.gettempdir(), timeout=15)

    def test_hpa_streams_file_and_stdin(self):
        for algorithm in ("hpa", "bihpa"):
            with self.subTest(algorithm=algorithm):
                result = self.run_cli(algorithm=algorithm)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertNotIn("not_implemented", result.stdout)
                self.assertIn("0 found 2", result.stdout)
                self.assertIn("3 invalid_vertex - 0", result.stdout)
                self.assertEqual(len(result.stdout.splitlines()), 10)
                streamed = self.run_cli(algorithm=algorithm, stdin=(self.fixture / "queries.txt").read_text())
                self.assertEqual(streamed.stdout, result.stdout)
                self.assertEqual(streamed.returncode, 1)

    def test_dijkstra_baseline_matches_optimized(self):
        optimized = self.run_cli(algorithm="dijkstra")
        baseline = self.run_cli(algorithm="dijkstra_baseline")
        self.assertEqual(baseline.returncode, 1, baseline.stderr)
        self.assertEqual(baseline.stdout, optimized.stdout)
        streamed = self.run_cli(algorithm="dijkstra_baseline", stdin=(self.fixture / "queries.txt").read_text())
        self.assertEqual(streamed.stdout, baseline.stdout)

    def test_hpa_stored_paths_match_across_workers(self):
        outputs = []
        for threads in (1, 4):
            result = subprocess.run([str(self.binary), "--graph", str(self.fixture / "graph.txt"),
                                     "--queries", str(self.fixture / "queries.txt"), "--algorithm", "hpa",
                                     "--threads", str(threads)],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1, result.stderr) # fixture has invalid IDs
            self.assertNotIn("Error:", result.stderr)
            outputs.append(result.stdout)
        self.assertEqual(*outputs)

    def test_rejects_removed_hpa_modes(self):
        for options in (["--hpa-workspace", "fresh"], ["--hpa-workspace", "reuse"],
                        ["--hpa-path-storage", "refine"], ["--hpa-path-storage", "trees"],
                        ["--hpa-path-storage", "paths"],
                        ["--hpa-weight", "1"]):
            result = subprocess.run([str(self.binary), "--graph", str(self.fixture / "graph.txt"),
                                     "--algorithm", "hpa"] + options,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Error:", result.stderr)

    def test_rejects_malformed_graphs(self):
        original = (self.fixture / "graph.txt").read_text()
        rows = original.splitlines()
        variants = [original.replace("BUSMAP_GRAPH 1", "BUSMAP_GRAPH 2"),
                    original.replace("WEIGHT_UNIT meter", "WEIGHT_UNIT minute"),
                    original.replace("DIRECTED 1", "DIRECTED 0"),
                    original.replace("COORDINATE_CRS EPSG:4326", "COORDINATE_CRS EPSG:3857"),
                    original.replace("NODES 6", "NODES 0"),
                    original.replace("NODES 6", "NODES 7"), original + "unexpected\n",
                    "\n".join(rows[:-1]) + "\n"]
        def altered(index, value):
            copy = rows.copy(); copy[index] = value
            return "\n".join(copy) + "\n"
        variants.extend([altered(1, "GRAPH_SHA256 invalid"),
                         altered(7, "1 10 106 0 0"), altered(7, "0 91 106 0 0"),
                         altered(7, "0 10 181 0 0"), altered(7, "0 nan 106 0 0"),
                         altered(7, "0 10 106 inf 0"), altered(8, "1 10 106 0 0"),
                         altered(14, "0 6 1"), altered(14, "0 0 1"),
                         altered(14, "0 1 -1"), altered(14, "0 1 inf"),
                         altered(14, "-1 1 1"), altered(14, "0 1 1 extra"),
                         altered(15, rows[14]), altered(15, "0 0 2")])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.txt"
            for i, text in enumerate(variants):
                with self.subTest(case=i):
                    path.write_text(text)
                    result = self.run_cli(graph=path)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("Error:", result.stderr)

    def test_query_validation(self):
        original = (self.fixture / "queries.txt").read_text()
        variants = [original.replace("BUSMAP_QUERIES 1", "BUSMAP_QUERIES 2"),
                    original.replace(original.splitlines()[1], "GRAPH_SHA256 " + "0" * 64),
                    original.replace("1 4 0", "0 4 0"),
                    original.replace("0 0 4", "0 0 4 extra"),
                    original.replace("0 0 4", "0 0 9223372036854775808"),
                    original + "0 0 4\n", "\n".join(original.splitlines()[:-1]) + "\n"]
        for i, text in enumerate(variants):
            with self.subTest(case=i):
                self.assertEqual(self.run_cli(stdin=text).returncode, 1)
        self.assertEqual(self.run_cli(algorithm="unknown").returncode, 1)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
