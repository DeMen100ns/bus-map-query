"""Pipeline/checker tests, independent of any user-written C++ shortest path."""
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from preprocessing.build_graph import GRAPH_PATH, RAW_PATH, init
from preprocessing.export_graph import export_graph
from preprocessing.io_utils import sha256
from verification.check import check
from verification.formats import read_queries, query_text
from verification.generate import generate
from verification.oracle import adjacency, reconstruct, shortest_paths

FIXTURE = ROOT / "tests/fixtures"


class HandoffTests(unittest.TestCase):
    def check_fixture(self, results=None, mode="optimal", answers=None, queries=None):
        return check(FIXTURE / "graph.json", queries or FIXTURE / "queries.txt",
                     answers or FIXTURE / "answers.json", results or FIXTURE / "valid_results.txt", mode)

    def test_oracle_known_graph(self):
        graph = adjacency(json.loads((FIXTURE / "graph.json").read_text()))
        distances, parents = shortest_paths(graph, 0)
        self.assertEqual(distances[:5], [0, 1, 1, 2, 2])
        self.assertEqual(reconstruct(0, 4, distances, parents), [0, 1, 3, 4])
        self.assertEqual(reconstruct(0, 0, distances, parents), [0])
        self.assertEqual(reconstruct(0, 5, distances, parents), [])
        reverse, _ = shortest_paths(graph, 4)
        self.assertEqual(reverse[0], math.inf)

    def test_oracle_against_independent_floyd_warshall(self):
        import random
        rng = random.Random(91)
        for _ in range(12):
            n = 7
            graph = [[] for _ in range(n)]
            expected = [[0 if i == j else math.inf for j in range(n)] for i in range(n)]
            for u in range(n):
                for v in range(n):
                    if u != v and rng.random() < .25:
                        w = rng.randint(0, 8)
                        graph[u].append((v, w)); expected[u][v] = w
            for k in range(n):
                for i in range(n):
                    for j in range(n):
                        expected[i][j] = min(expected[i][j], expected[i][k] + expected[k][j])
            for source in range(n):
                distances, parents = shortest_paths(graph, source)
                self.assertEqual(distances, expected[source])
                weights = {(u, v): w for u, row in enumerate(graph) for v, w in row}
                for target in range(n):
                    path = reconstruct(source, target, distances, parents)
                    if path:
                        self.assertEqual(sum(weights[p] for p in zip(path, path[1:])), distances[target])

    def test_checker_accepts_tied_paths(self):
        self.assertTrue(self.check_fixture()["ok"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.txt"
            path.write_text((FIXTURE / "valid_results.txt").read_text().replace("0 1 3 4", "0 2 3 4"))
            self.assertTrue(self.check_fixture(path)["ok"])

    def test_checker_rejects_wrong_paths_statuses_and_distances(self):
        original = (FIXTURE / "valid_results.txt").read_text()
        variants = [original.replace("0 found 2 4 0 1 3 4", "0 found 2 3 0 2 4"),
                    original.replace("0 found 2 4 0 1 3 4", "0 found 2 4 4 3 1 0"),
                    original.replace("0 found 2 4 0 1 3 4", "0 found 1 4 0 1 3 4"),
                    original.replace("0 found 2 4 0 1 3 4", "0 unreachable - 0"),
                    original.replace("0 found 2 4 0 1 3 4", "0 not_implemented - 0"),
                    original.replace("2 found 0 1 3", "2 found 0 2 3 3"),
                    original.replace("1 unreachable - 0", "1 found 0 2 4 0")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.txt"
            for i, text in enumerate(variants):
                with self.subTest(case=i):
                    path.write_text(text)
                    self.assertFalse(self.check_fixture(path)["ok"])
                    self.assertFalse(self.check_fixture(path, "any")["ok"])

    def test_any_mode_reports_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.txt"
            path.write_text((FIXTURE / "valid_results.txt").read_text().replace(
                "0 found 2 4 0 1 3 4", "0 found 5 3 0 3 4"))
            self.assertFalse(self.check_fixture(path)["ok"])
            report = self.check_fixture(path, "any")
            self.assertTrue(report["ok"])
            self.assertEqual(report["max_gap_percent"], 150)

    def test_mode_aliases_and_unknown_mode(self):
        self.assertEqual(self.check_fixture(mode="exact"), self.check_fixture(mode="optimal"))
        self.assertEqual(self.check_fixture(mode="approximate"), self.check_fixture(mode="any"))
        with self.assertRaises(ValueError):
            self.check_fixture(mode="unknown")

    def test_any_accepts_positive_path_when_optimum_is_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            document = json.loads((FIXTURE / "graph.json").read_text())
            for edge in document["edges"]:
                if (edge["source"], edge["target"]) in {(0, 1), (1, 3)}:
                    edge["distance_m"] = 0
            graph = root / "graph.json"
            graph.write_text(json.dumps(document))
            digest = sha256(graph)
            queries = root / "queries.txt"
            queries.write_text(query_text(digest, {0: (0, 3)}))
            answers = root / "answers.json"
            answers.write_text(json.dumps({"schema_version": 1, "graph_sha256": digest,
                "queries_sha256": sha256(queries),
                "answers": [{"query_id": 0, "status": "found", "distance_m": 0}]}))
            results = root / "results.txt"
            results.write_text(f"BUSMAP_RESULTS 1\nGRAPH_SHA256 {digest}\nRESULTS 1\n0 found 5 2 0 3\n")
            self.assertFalse(check(graph, queries, answers, results, "optimal")["ok"])
            report = check(graph, queries, answers, results, "any")
            self.assertTrue(report["ok"])
            self.assertIsNone(report["max_gap_percent"])
            self.assertEqual(report["undefined_relative_gap_count"], 1)
            self.assertEqual(report["max_extra_distance_m"], 5)
            self.assertEqual(report["optimal_found_count"], 0)
            # CLI accepts both public modes and propagates their success/failure.
            for mode, expected_exit in (("optimal", 1), ("any", 0)):
                command = [sys.executable, str(ROOT / "scripts/busmap_data.py"), "check",
                    "--graph", str(graph), "--queries", str(queries), "--answers", str(answers),
                    "--results", str(results), "--mode", mode]
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, expected_exit, result.stderr)
                self.assertEqual(json.loads(result.stdout)["mode"], mode)

    def test_checker_rejects_malformed_records_and_hashes(self):
        original = (FIXTURE / "valid_results.txt").read_text()
        variants = [original.replace("0 found 2", "1 found 2"),
                    original.replace("0 found 2", "0 found nan"),
                    original.replace("1 unreachable - 0", "1 unreachable 0 0"),
                    original.replace("RESULTS 7", "RESULTS 8"),
                    original.replace(original.splitlines()[1], "GRAPH_SHA256 " + "0" * 64),
                    original + "extra\n"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.txt"
            for text in variants:
                path.write_text(text)
                with self.assertRaises(ValueError): self.check_fixture(path)
            answers = json.loads((FIXTURE / "answers.json").read_text())
            answers["queries_sha256"] = "0" * 64
            answer_path = Path(tmp) / "answers.json"
            answer_path.write_text(json.dumps(answers))
            with self.assertRaises(ValueError): self.check_fixture(answers=answer_path)

    def test_full_export_preserves_every_node_and_edge_and_is_reproducible(self):
        document = json.loads(GRAPH_PATH.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            output, manifest = Path(tmp) / "graph.txt", Path(tmp) / "manifest.json"
            export_graph(GRAPH_PATH, output, manifest)
            self.assertEqual(output.read_bytes(), (ROOT / "data/processed/graph.txt").read_bytes())
            self.assertEqual(manifest.read_bytes(), (ROOT / "data/processed/graph.manifest.json").read_bytes())
            lines = iter(output.read_text().splitlines())
            for _ in range(7): next(lines)
            for node in document["nodes"]:
                node_id, lat, lon, x, y = next(lines).split()
                self.assertEqual((int(node_id), float(lat), float(lon)), (node["id"], node["lat"], node["lon"]))
                self.assertTrue(math.isfinite(float(x)) and math.isfinite(float(y)))
            self.assertEqual(next(lines), f"EDGES {len(document['edges'])}")
            for edge in document["edges"]:
                u, v, w = next(lines).split()
                self.assertEqual((int(u), int(v), float(w)), (edge["source"], edge["target"], edge["distance_m"]))
            self.assertEqual(list(lines), [])

    def test_prevents_input_overwrite_and_raw_output(self):
        raw_hash = sha256(RAW_PATH)
        graph_hash = sha256(GRAPH_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            with self.assertRaises(ValueError): export_graph(GRAPH_PATH, GRAPH_PATH, manifest)
            with self.assertRaises(ValueError): export_graph(GRAPH_PATH, RAW_PATH, manifest)
            with self.assertRaises(ValueError): init(RAW_PATH, RAW_PATH)
            alias = Path(tmp) / "alias.json"
            alias.symlink_to(GRAPH_PATH)
            with self.assertRaises(ValueError): export_graph(GRAPH_PATH, alias, manifest)
            with self.assertRaises(ValueError): export_graph(GRAPH_PATH, manifest, manifest)
        self.assertEqual(sha256(RAW_PATH), raw_hash)
        self.assertEqual(sha256(GRAPH_PATH), graph_hash)

    def test_query_suite_reproducibility_and_truth(self):
        with tempfile.TemporaryDirectory() as tmp:
            generate(output_dir=tmp)
            for name in ("queries.txt", "answers.json", "manifest.json"):
                self.assertEqual((Path(tmp) / name).read_bytes(), (ROOT / "benchmarks" / name).read_bytes())
        digest, queries = read_queries(ROOT / "benchmarks/queries.txt")
        answers = json.loads((ROOT / "benchmarks/answers.json").read_text())["answers"]
        self.assertEqual(digest, sha256(GRAPH_PATH))
        self.assertEqual(len(queries), 1000)
        self.assertEqual(len(set(queries.values())), 1000)
        self.assertEqual(Counter(a["group"] for a in answers), {"short": 334, "medium": 333, "long": 333})
        self.assertLessEqual(max(Counter(s for s, _ in queries.values()).values()), 10)
        graph = adjacency(json.loads(GRAPH_PATH.read_text()))
        grouped = defaultdict(list)
        for answer in answers:
            source, target = queries[answer["query_id"]]
            grouped[source].append((target, answer["distance_m"]))
        for source, targets in grouped.items():
            distances, _ = shortest_paths(graph, source)
            for target, expected in targets:
                self.assertEqual(distances[target], expected)
                self.assertTrue(math.isfinite(expected))

    def test_entrypoint_from_other_working_directory(self):
        command = [sys.executable, str(ROOT / "scripts/busmap_data.py"), "check", "--graph", str(FIXTURE / "graph.json"),
                   "--queries", str(FIXTURE / "queries.txt"), "--answers", str(FIXTURE / "answers.json"),
                   "--results", str(FIXTURE / "valid_results.txt")]
        result = subprocess.run(command, cwd=tempfile.gettempdir(), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
