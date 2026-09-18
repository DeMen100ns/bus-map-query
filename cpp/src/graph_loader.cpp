#include "busmap/graph_loader.hpp"
#include "text_reader.hpp"
#include <fstream>
#include <limits>
#include <utility>

namespace busmap {
Graph load_graph(const std::filesystem::path& path) {
    std::ifstream input(path);
    if (!input) throw std::runtime_error("Cannot open graph: " + path.string());
    detail::TextReader reader(input);
    Graph graph;
    reader.expect("BUSMAP_GRAPH", "1");
    graph.graph_sha256_ = reader.field("GRAPH_SHA256");
    if (!detail::valid_hash(graph.graph_sha256_)) throw std::runtime_error("Invalid graph SHA-256 tag");
    reader.expect("COORDINATE_CRS", "EPSG:4326");
    reader.expect("DISTANCE_CRS", "EPSG:3405");
    reader.expect("WEIGHT_UNIT", "meter");
    reader.expect("DIRECTED", "1");
    auto nodes = detail::integer<std::uint64_t>(reader.field("NODES"));
    if (nodes == 0 || nodes > std::numeric_limits<NodeId>::max()) throw std::runtime_error("Unsupported node count");
    std::pair<double, double> previous_coordinate;
    for (std::uint64_t i = 0; i < nodes; ++i) {
        auto values = reader.row(5);
        if (detail::integer<std::uint64_t>(values[0]) != i) throw std::runtime_error("Non-dense or unordered node IDs");
        Coordinate c{detail::number(values[1]), detail::number(values[2]),
                     detail::number(values[3]), detail::number(values[4])};
        if (c.lat < -90 || c.lat > 90 || c.lon < -180 || c.lon > 180) throw std::runtime_error("Coordinate out of range");
        auto coordinate = std::pair{c.lat, c.lon};
        if (i && coordinate <= previous_coordinate) throw std::runtime_error("Coordinates must be unique and sorted");
        previous_coordinate = coordinate;
        graph.coordinates_.push_back(c);
    }
    graph.offsets_.assign(static_cast<std::size_t>(nodes) + 1, 0);
    auto edges = detail::integer<std::uint64_t>(reader.field("EDGES"));
    if (edges > graph.edges_.max_size()) throw std::runtime_error("Unsupported edge count");
    std::pair<NodeId, NodeId> previous_edge;
    for (std::uint64_t i = 0; i < edges; ++i) {
        auto values = reader.row(3);
        auto u = detail::integer<NodeId>(values[0]);
        auto v = detail::integer<NodeId>(values[1]);
        auto weight = detail::number(values[2]);
        if (u >= nodes || v >= nodes || u == v || weight < 0) throw std::runtime_error("Invalid edge endpoint or weight");
        auto edge = std::pair{u, v};
        if (i && edge <= previous_edge) throw std::runtime_error("Edges must be unique and sorted");
        previous_edge = edge;
        graph.edges_.push_back({v, weight});
        ++graph.offsets_[u + 1];
    }
    for (std::size_t i = 1; i < graph.offsets_.size(); ++i) graph.offsets_[i] += graph.offsets_[i - 1];
    reader.finish();
    return graph;
}
}
