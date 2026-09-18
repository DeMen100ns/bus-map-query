#include "busmap/graph.hpp"
#include <stdexcept>

namespace busmap {
const Coordinate& Graph::coordinate(NodeId node) const {
    return coordinates_.at(node);
}

std::span<const Edge> Graph::outgoing(NodeId node) const {
    if (node >= node_count()) throw std::out_of_range("Invalid graph vertex");
    return std::span<const Edge>(edges_).subspan(offsets_[node], offsets_[node + 1] - offsets_[node]);
}
}
