#pragma once
#include "busmap/query.hpp"

namespace busmap {
// Fresh local distance, parent and heap storage for each valid query.
PathResult dijkstra_baseline(const Graph& graph, const Query& query);
} // namespace busmap
