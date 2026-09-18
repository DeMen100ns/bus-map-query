#pragma once

#include <cstdint>
#include <filesystem>
#include <span>
#include <string>
#include <vector>

namespace busmap {
using NodeId = std::uint32_t;

struct Coordinate {
    double lat, lon, x_m, y_m;
};

struct Edge {
    NodeId target;
    double distance_m;
};

// Immutable public view. Per-query distances, parents and queues do not belong here.
class Graph {
public:
    [[nodiscard]] std::size_t node_count() const noexcept { return coordinates_.size(); }
    [[nodiscard]] std::size_t edge_count() const noexcept { return edges_.size(); }
    [[nodiscard]] const std::string& graph_sha256() const noexcept { return graph_sha256_; }
    [[nodiscard]] const Coordinate& coordinate(NodeId node) const;
    [[nodiscard]] std::span<const Edge> outgoing(NodeId node) const;

private:
    Graph() = default;
    friend Graph load_graph(const std::filesystem::path& path);
    std::string graph_sha256_;
    std::vector<Coordinate> coordinates_;
    std::vector<std::size_t> offsets_;
    std::vector<Edge> edges_;
};
} // namespace busmap
