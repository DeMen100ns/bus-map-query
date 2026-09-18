#pragma once
#include "busmap/graph.hpp"
#include <cstdint>
#include <optional>
#include <string_view>
#include <vector>

namespace busmap {
struct Query {
    std::uint64_t id;
    std::int64_t source;
    std::int64_t target;
};

enum class PathStatus { Found, Unreachable, InvalidVertex, NotImplemented };

struct PathResult {
    PathStatus status = PathStatus::NotImplemented;
    std::optional<double> distance_m;
    std::vector<NodeId> path;
};

std::string_view status_name(PathStatus status);
}
