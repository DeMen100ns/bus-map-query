"""Export canonical graph JSON to the dependency-free C++ text format."""
import json
import math
from pathlib import Path

from preprocessing.build_graph import GRAPH_PATH, validate_document
from preprocessing.graph import projected_transformer
from preprocessing.io_utils import ROOT, atomic_text, json_text, protect_outputs, sha256

TEXT_PATH = ROOT / "data/processed/graph.txt"
MANIFEST_PATH = ROOT / "data/processed/graph.manifest.json"


def export_graph(input_path=GRAPH_PATH, output_path=TEXT_PATH, manifest_path=MANIFEST_PATH):
    import pyproj

    protect_outputs([output_path, manifest_path], [input_path, GRAPH_PATH])
    document = json.loads(Path(input_path).read_text(encoding="utf-8"))
    validate_document(document)
    digest = sha256(input_path)
    lines = ["BUSMAP_GRAPH 1", f"GRAPH_SHA256 {digest}",
             "COORDINATE_CRS EPSG:4326", "DISTANCE_CRS EPSG:3405",
             "WEIGHT_UNIT meter", "DIRECTED 1", f"NODES {len(document['nodes'])}"]
    transformer = projected_transformer()
    for node in document["nodes"]:
        x, y = transformer.transform(node["lon"], node["lat"], errcheck=True)
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("Projection produced non-finite coordinates")
        values = (node["lat"], node["lon"], x, y)
        lines.append(f"{node['id']} " + " ".join(format(v, ".17g") for v in values))
    lines.append(f"EDGES {len(document['edges'])}")
    for edge in document["edges"]:
        lines.append(f"{edge['source']} {edge['target']} {edge['distance_m']:.17g}")
    atomic_text(output_path, "\n".join(lines) + "\n")
    manifest = {
        "format": "BUSMAP_GRAPH", "version": 1, "graph_sha256": digest,
        "files": {"canonical": {"name": Path(input_path).name, "sha256": digest},
                  "text": {"name": Path(output_path).name, "sha256": sha256(output_path)}},
        "node_count": len(document["nodes"]), "edge_count": len(document["edges"]),
        "source": document.get("source", {}), "collected_at": None,
        "snapshot_note": "Historical HCMC geometry; collection date unknown; not a live transit network.",
        "generator": {"name": "busmap-data export", "version": 1,
                      "pyproj_version": pyproj.__version__, "proj_version": pyproj.proj_version_str},
    }
    atomic_text(manifest_path, json_text(manifest))
    return manifest
