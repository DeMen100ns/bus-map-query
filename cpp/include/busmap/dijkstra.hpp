#pragma once
#include "busmap/query.hpp"
#include "busmap/search_workspace.hpp"

namespace busmap {
// Reuses per-worker distance, parent and heap storage; resets only touched nodes.
PathResult dijkstra(const Graph& graph, const Query& query, SearchWorkspace& workspace);
}
