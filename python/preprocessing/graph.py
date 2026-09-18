"""In-memory graph objects shared by the existing routing implementations."""
import json
import math
import os
from pathlib import Path
import tempfile

COORDINATE_CRS = "EPSG:4326"
DISTANCE_CRS = "EPSG:3405"


def projected_transformer():
    """Import pyproj only when building distances, not when loading a graph."""
    from pyproj import Transformer
    return Transformer.from_crs(COORDINATE_CRS, DISTANCE_CRS, always_xy=True)


class Node:
    def __init__(self, id, lat, lng):
        self._id = id
        self._lat = lat
        self._lng = lng  # Existing algorithm API; the on-disk field is named lon.

    def convert_latlng_to_xy(self):
        return projected_transformer().transform(self._lng, self._lat, errcheck=True)


def distance(x1, y1, x2, y2):
    return math.hypot(x1 - x2, y1 - y2)


class Edge:
    def __init__(self, start, stop, length=-1):
        self._start = start
        self._stop = stop
        if length == -1:
            self._length = distance(*start.convert_latlng_to_xy(), *stop.convert_latlng_to_xy())
        else:
            self._length = length


class Graph:
    def __init__(self, vertices_list, edges_list, metadata=None):
        self._vertices_list = vertices_list
        self._edges_list = edges_list
        self.metadata = dict(metadata or {})

    def to_document(self):
        document = {
            "schema_version": 1,
            "directed": True,
            "coordinate_crs": COORDINATE_CRS,
            "distance_crs": DISTANCE_CRS,
            "weight_unit": "meter",
            **self.metadata,
        }
        document["nodes"] = [
            {"id": v._id, "lat": v._lat, "lon": v._lng}
            for v in self._vertices_list
        ]
        document["edges"] = [
            {"source": e._start._id, "target": e._stop._id, "distance_m": e._length}
            for e in self._edges_list
        ]
        return document

    def OutputAsJSON(self, path):
        """Write standard JSON atomically; retain the original public method name."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=path.name + ".", suffix=".tmp", delete=False) as f:
                temporary = Path(f.name)
                json.dump(self.to_document(), f, ensure_ascii=False, allow_nan=False, indent=2)
                f.write("\n")
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
