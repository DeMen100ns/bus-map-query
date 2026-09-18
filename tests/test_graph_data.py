"""Regression tests for graph data normalization; no benchmark is run on import."""
import copy
import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from preprocessing.build_graph import (GRAPH_PATH, RAW_PATH, build_graph, graph_building_from_file,
                            read_routes, validate_document)
from reference.dijkstra import Dijkstra_Shortest_Path


def fixture_document():
    return {
        "schema_version": 1, "directed": True, "coordinate_crs": "EPSG:4326",
        "distance_crs": "EPSG:3405", "weight_unit": "meter",
        "nodes": [{"id": i, "lat": 10 + i * 0.01, "lon": 106} for i in range(5)],
        "edges": [{"source": u, "target": v, "distance_m": w}
                  for u, v, w in [(0, 1, 3), (0, 2, 1), (1, 3, 1), (2, 1, 1), (2, 3, 10)]],
    }


class SchemaTests(unittest.TestCase):
    def test_known_shortest_path_and_unreachable_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "graph.json"
            path.write_text(json.dumps(fixture_document()))
            graph = graph_building_from_file(path)
        adjacency = {v._id: [] for v in graph._vertices_list}
        for edge in graph._edges_list:
            adjacency[edge._start._id].append((edge._stop._id, edge._length))
        self.assertEqual(Dijkstra_Shortest_Path(0, 3, graph, adjacency), (3, [0, 2, 1, 3]))
        self.assertEqual(Dijkstra_Shortest_Path(0, 4, graph, adjacency), (float("inf"), []))
        self.assertEqual(Dijkstra_Shortest_Path(2, 2, graph, adjacency), (0, [2]))

    def test_loader_preserves_coordinate_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "graph.json"
            path.write_text(json.dumps(fixture_document()))
            node = graph_building_from_file(path)._vertices_list[0]
        self.assertEqual((node._lat, node._lng), (10, 106))

    def test_rejects_invalid_graphs(self):
        mutations = [
            lambda d: d.update(schema_version=2),
            lambda d: d.update(schema_version=True),
            lambda d: d.update(directed=False),
            lambda d: d.update(weight_unit="minute"),
            lambda d: d["nodes"][1].update(id=0),
            lambda d: d["nodes"][1].update(lat=10, lon=106),
            lambda d: d["nodes"][0].update(lat=float("nan")),
            lambda d: d["nodes"][0].update(lon=181),
            lambda d: d["edges"][0].update(target=5),
            lambda d: d["edges"][0].update(source=True),
            lambda d: d["edges"][0].update(target=0),
            lambda d: d["edges"][0].update(distance_m=-1),
            lambda d: d["edges"][0].update(distance_m=float("inf")),
            lambda d: d["edges"].insert(1, copy.deepcopy(d["edges"][0])),
            lambda d: d.update(statistics={"node_count": 100, "edge_count": 5}),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutations.index(mutation)):
                doc = fixture_document()
                mutation(doc)
                with self.assertRaises(ValueError):
                    validate_document(doc)

    def test_zero_weight_between_distinct_nodes_is_allowed(self):
        doc = fixture_document()
        doc["edges"][0]["distance_m"] = 0
        validate_document(doc)

    def test_invalid_raw_coordinates_fail_explicitly(self):
        cases = [
            {"lat": [10, 11], "lng": [106], "RouteId": "1", "RouteVarId": "1"},
            {"lat": [91], "lng": [106], "RouteId": "1", "RouteVarId": "1"},
            {"lat": [True], "lng": [106], "RouteId": "1", "RouteVarId": "1"},
            {"lat": [], "lng": [], "RouteId": "1", "RouteVarId": "1"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paths.jsonl"
            for row in cases:
                with self.subTest(row=row):
                    path.write_text(json.dumps(row) + "\n")
                    with self.assertRaises(ValueError):
                        read_routes(path)


class BuilderTests(unittest.TestCase):
    def test_nearby_coordinates_are_not_merged(self):
        # The two coordinates whose IDs collided in the original dataset.
        row = {"RouteId": "1", "RouteVarId": "1", "lat": [10.74339581] * 4,
               "lng": [106.62136078, 106.62136078, 106.6213608, 106.62136078]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paths.jsonl"
            path.write_text((json.dumps(row) + "\n") * 2)
            graph = build_graph(path)
        self.assertEqual(len(graph._vertices_list), 2)
        self.assertEqual({(e._start._id, e._stop._id) for e in graph._edges_list}, {(0, 1), (1, 0)})
        self.assertTrue(all(e._length > 0 for e in graph._edges_list))
        self.assertEqual(graph.metadata["statistics"]["self_loop_segments_removed"], 2)
        self.assertEqual(graph.metadata["statistics"]["duplicate_directed_segments_merged"], 2)

    def test_direction_distance_scale_and_singleton_node(self):
        rows = [
            {"RouteId": "1", "RouteVarId": "1", "lat": [10.767676, 10.768676], "lng": [106.689362] * 2},
            {"RouteId": "2", "RouteVarId": "2", "lat": [11], "lng": [107]},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paths.jsonl"
            path.write_text("\n".join(map(json.dumps, rows)) + "\n")
            graph = build_graph(path)
        self.assertEqual(len(graph._vertices_list), 3)
        self.assertEqual(len(graph._edges_list), 1)
        edge = graph._edges_list[0]
        self.assertEqual((edge._start._id, edge._stop._id), (0, 1))
        # A 0.001 degree latitude step near HCMC is roughly 111 metres.
        self.assertGreater(edge._length, 100)
        self.assertLess(edge._length, 120)

    def test_complete_dataset_matches_raw_directed_segments(self):
        document = json.loads(GRAPH_PATH.read_text())
        validate_document(document)
        rows = read_routes()
        coordinates = {(lat, lon) for row in rows for lat, lon in zip(row["lat"], row["lng"])}
        actual_coordinates = {(n["lat"], n["lon"]) for n in document["nodes"]}
        self.assertEqual(actual_coordinates, coordinates)
        expected = set()
        for row in rows:
            points = list(zip(row["lat"], row["lng"]))
            expected.update((a, b) for a, b in zip(points, points[1:]) if a != b)
        nodes = document["nodes"]
        actual = {((nodes[e["source"]]["lat"], nodes[e["source"]]["lon"]),
                   (nodes[e["target"]]["lat"], nodes[e["target"]]["lon"])) for e in document["edges"]}
        self.assertEqual(actual, expected)
        self.assertEqual((len(nodes), len(document["edges"])), (38148, 42170))
        self.assertEqual(document["source"]["sha256"], hashlib.sha256(RAW_PATH.read_bytes()).hexdigest())

    def test_rebuild_is_byte_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "graph.json"
            build_graph().OutputAsJSON(output)
            self.assertEqual(output.read_bytes(), GRAPH_PATH.read_bytes())

    def test_routing_modules_can_load_new_format(self):
        for name in ["reference.dijkstra", "reference.astar", "reference.hierarchical"]:
            with self.subTest(module=name):
                module = importlib.import_module(name)
                graph, adjacency = module.get_graph()
                self.assertEqual(len(graph._vertices_list), 38148)
                self.assertEqual(sum(map(len, adjacency.values())), 42170)
                if hasattr(module, "mp"):
                    first = graph._vertices_list[0]
                    self.assertEqual((module.mp[0]._lat, module.mp[0]._lng), (first._lat, first._lng))


if __name__ == "__main__":
    unittest.main()
