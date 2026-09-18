"""Build and validate BusMap graph schema v1 from unchanged route JSONL data."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from preprocessing.graph import COORDINATE_CRS, DISTANCE_CRS, Node, Edge, Graph, distance, projected_transformer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_PATH = PROJECT_ROOT / "data/raw/paths.jsonl"
GRAPH_PATH = PROJECT_ROOT / "data/processed/graph.json"


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_coordinate(lat, lon, context):
    if not finite_number(lat) or not -90 <= lat <= 90:
        raise ValueError(f"{context}: invalid latitude {lat!r}")
    if not finite_number(lon) or not -180 <= lon <= 180:
        raise ValueError(f"{context}: invalid longitude {lon!r}")


def read_routes(path=RAW_PATH):
    """Validate each JSONL route; do not silently zip unequal coordinate lists."""
    routes = []
    with Path(path).open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Line {lineno}: invalid JSON") from exc
            if not isinstance(row, dict) or not {"lat", "lng", "RouteId", "RouteVarId"} <= row.keys():
                raise ValueError(f"Line {lineno}: missing route fields")
            if not all(isinstance(row[k], str) and row[k] for k in ("RouteId", "RouteVarId")):
                raise ValueError(f"Line {lineno}: route IDs must be nonempty strings")
            latitudes, longitudes = row["lat"], row["lng"]
            if not isinstance(latitudes, list) or not isinstance(longitudes, list):
                raise ValueError(f"Line {lineno}: coordinates must be arrays")
            if not latitudes or len(latitudes) != len(longitudes):
                raise ValueError(f"Line {lineno}: coordinate arrays must be nonempty and equally sized")
            for lat, lon in zip(latitudes, longitudes):
                validate_coordinate(lat, lon, f"Line {lineno}")
            routes.append(row)
    if not routes:
        raise ValueError("Input contains no routes")
    return routes


def build_graph(path=RAW_PATH):
    import pyproj

    path = Path(path)
    routes = read_routes(path)
    coordinates = sorted({(lat, lon) for row in routes for lat, lon in zip(row["lat"], row["lng"])})
    # Exact coordinate pairs are keys. Nearby but distinct coordinates stay distinct.
    ids = {coordinate: i for i, coordinate in enumerate(coordinates)}
    vertices = [Node(i, lat, lon) for i, (lat, lon) in enumerate(coordinates)]
    transformer = projected_transformer()
    xy = [transformer.transform(lon, lat, errcheck=True) for lat, lon in coordinates]
    if not all(math.isfinite(x) and math.isfinite(y) for x, y in xy):
        raise ValueError("Projection produced a non-finite coordinate")

    weights = {}
    input_segments = self_loops = duplicate_edges = 0
    for row in routes:
        points = list(zip(row["lat"], row["lng"]))
        for start, stop in zip(points, points[1:]):
            input_segments += 1
            u, v = ids[start], ids[stop]
            if u == v:
                self_loops += 1
                continue
            weight = distance(*xy[u], *xy[v])
            if not math.isfinite(weight) or weight < 0:
                raise ValueError(f"Invalid distance on {u} -> {v}")
            key = (u, v)
            if key in weights:
                duplicate_edges += 1
                weights[key] = min(weights[key], weight)
            else:
                weights[key] = weight
    edges = [Edge(vertices[u], vertices[v], w) for (u, v), w in sorted(weights.items())]
    try:
        source_name = path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        source_name = path.name
    metadata = {
        "distance_method": "euclidean_projected",
        "node_id_policy": "dense_ids_sorted_by_lat_lon",
        "coordinate_merge_policy": "exact_pair_no_rounding_or_snapping",
        "edge_policy": {"self_loops": "removed", "duplicate_directed_pairs": "minimum_distance"},
        "source": {"path": source_name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
        "generator": {"name": "BusMap graph_building.py", "version": 1,
                      "pyproj_version": pyproj.__version__, "proj_version": pyproj.proj_version_str},
        "statistics": {"route_records": len(routes),
                       "coordinate_samples": sum(len(row["lat"]) for row in routes),
                       "input_segments": input_segments, "self_loop_segments_removed": self_loops,
                       "duplicate_directed_segments_merged": duplicate_edges,
                       "node_count": len(vertices), "edge_count": len(edges)},
    }
    graph = Graph(vertices, edges, metadata)
    validate_document(graph.to_document())
    return graph


def validate_document(document):
    """Validate v1 structure and cross-record invariants before algorithms see it."""
    if not isinstance(document, dict):
        raise ValueError("Graph must be a JSON object")
    if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
        raise ValueError("Unsupported graph schema_version; expected 1")
    if document.get("directed") is not True:
        raise ValueError("Schema v1 requires a directed graph")
    for field, expected in (("coordinate_crs", COORDINATE_CRS), ("distance_crs", DISTANCE_CRS),
                            ("weight_unit", "meter")):
        if document.get(field) != expected:
            raise ValueError(f"Expected {field}={expected!r}")
    nodes, edges = document.get("nodes"), document.get("edges")
    if not isinstance(nodes, list) or not nodes or not isinstance(edges, list):
        raise ValueError("Expected a nonempty nodes array and an edges array")
    coordinates = []
    for i, node in enumerate(nodes):
        if not isinstance(node, dict) or type(node.get("id")) is not int or node["id"] != i:
            raise ValueError("Node IDs must be unique, dense, and in array order starting at zero")
        lat, lon = node.get("lat"), node.get("lon")
        validate_coordinate(lat, lon, f"Node {i}")
        coordinates.append((lat, lon))
    if coordinates != sorted(set(coordinates)):
        raise ValueError("Nodes must contain unique exact coordinates sorted by (lat, lon)")
    previous = None
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("An edge must be an object")
        u, v, w = edge.get("source"), edge.get("target"), edge.get("distance_m")
        if any(type(i) is not int or not 0 <= i < len(nodes) for i in (u, v)):
            raise ValueError("Edge refers to an invalid node ID")
        if u == v:
            raise ValueError("Self-loops are excluded from schema v1")
        if not finite_number(w) or w < 0:
            raise ValueError("Edge distance must be finite and nonnegative")
        if previous is not None and (u, v) <= previous:
            raise ValueError("Edges must be sorted, with no duplicate directed pairs")
        previous = (u, v)
    statistics = document.get("statistics")
    if statistics is not None:
        if not isinstance(statistics, dict) or statistics.get("node_count") != len(nodes) or statistics.get("edge_count") != len(edges):
            raise ValueError("Graph statistics do not match its arrays")


def graph_building_from_file(path=GRAPH_PATH):
    """Load schema v1 without needing pyproj or depending on the working directory."""
    with Path(path).open(encoding="utf-8") as f:
        document = json.load(f)
    validate_document(document)
    vertices = [Node(n["id"], n["lat"], n["lon"]) for n in document["nodes"]]
    edges = [Edge(vertices[e["source"]], vertices[e["target"]], e["distance_m"])
             for e in document["edges"]]
    metadata = {k: v for k, v in document.items() if k not in ("nodes", "edges")}
    return Graph(vertices, edges, metadata)


def init(input_path=RAW_PATH, output_path=GRAPH_PATH):
    from preprocessing.io_utils import protect_outputs
    protect_outputs([output_path], [input_path])
    graph = build_graph(input_path)
    graph.OutputAsJSON(output_path)
    return graph


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=RAW_PATH)
    parser.add_argument("--output", type=Path, default=GRAPH_PATH)
    args = parser.parse_args()
    result = init(args.input, args.output)
    print(json.dumps(result.metadata["statistics"], indent=2))
    print(f"Written: {args.output}")
